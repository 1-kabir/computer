<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import { mount, unmount } from 'svelte';
	import { Editor } from '@tiptap/core';
	import StarterKit from '@tiptap/starter-kit';
	import { Markdown } from '@tiptap/markdown';
	import Placeholder from '@tiptap/extension-placeholder';
	import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
	import { all, createLowlight } from 'lowlight';
	import { toast } from 'svelte-sonner';

	import { createFileMention, extractMentionedFiles, type FileMentionAttrs } from './FileMention';
	import {
		createSkillMention,
		extractMentionedSkills,
		type SkillMentionAttrs
	} from './SkillMention';
	import FileSuggestionPopup from './FileSuggestionPopup.svelte';
	import SkillSuggestionPopup from './SkillSuggestionPopup.svelte';
	import { searchFiles } from '$lib/apis/files';
	import { getSkills } from '$lib/apis/skills';
	import { uploadFile } from '$lib/apis/files';
	import type { ChatTask, ContextUsage } from '$lib/apis/chat';
	import ModelSelector from '../common/ModelSelector.svelte';
	import SendButton from './SendButton.svelte';
	import PlusMenu from './PlusMenu.svelte';
	import DictateButton from './DictateButton.svelte';
	import QueuedMessageItem from './QueuedMessageItem.svelte';
	import Tasks from './Tasks.svelte';
	import AskUserCard from './AskUserCard.svelte';
	import Icon from '../Icon.svelte';
	import type { ToolApprovalMode } from '$lib/apis/chat';
	import {
		sttConfigured,
		ttsConfigured,
		ttsEnabled,
		unlockTtsAudioPlayback,
		voiceModeSttMode
	} from '$lib/stores/audio';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { t } from '$lib/i18n';
	import { TAB_DRAG_MIME } from '$lib/constants';
	import { tooltip } from '$lib/tooltip';

	// Keep screen awake during voice mode conversations
	let voiceWakeLock: WakeLockSentinel | null = null;

	async function acquireVoiceWakeLock() {
		if (voiceWakeLock) return;
		try {
			if ('wakeLock' in navigator) {
				voiceWakeLock = await navigator.wakeLock.request('screen');
				voiceWakeLock.addEventListener('release', () => {
					voiceWakeLock = null;
				});
			}
		} catch {}
	}

	function releaseVoiceWakeLock() {
		voiceWakeLock?.release().catch(() => {});
		voiceWakeLock = null;
	}

	interface Props {
		inputText: string;
		selectedModel: string;
		toolApprovalMode?: ToolApprovalMode;
		planMode?: boolean;
		requestParams?: Record<string, unknown>;
		voiceModeEnabled?: boolean;
		sending: boolean;
		streaming?: boolean;
		workspace?: string;
		placeholder?: string;
		contextUsage?: ContextUsage | null;
		tasks?: ChatTask[];
		askUser?: any;
		queuedMessages?: { id: string; content: string }[];
		hasChatContent?: boolean;
		onsend: () => void;
		oncompact?: () => void;
		onfork?: () => void;
		onplan?: () => void;
		onstatus?: () => void;
		onskillslist?: () => void;
		oncancel?: () => void;
		onaskuseranswer?: (
			messageId: string,
			callId: string,
			answers: Record<string, string>,
			timedOut: boolean
		) => void;
		onqueuesendnow?: (id: string) => void;
		onqueueedit?: (id: string) => void;
		onqueuedelete?: (id: string) => void;
		onsettingschange?: () => void;
		ontoolapprovalchange?: (mode: ToolApprovalMode) => void;
	}
	let {
		inputText = $bindable(),
		selectedModel = $bindable(),
		toolApprovalMode = $bindable('auto'),
		planMode = $bindable(false),
		requestParams = $bindable({}),
		voiceModeEnabled = $bindable(false),
		sending,
		streaming = false,
		workspace = '',
		placeholder = 'Message...',
		contextUsage = null,
		tasks = [],
		askUser = null,
		queuedMessages = [],
		hasChatContent = false,
		onsend,
		oncompact,
		onfork,
		onplan,
		onstatus,
		onskillslist,
		oncancel,
		onaskuseranswer,
		onqueuesendnow,
		onqueueedit,
		onqueuedelete,
		onsettingschange,
		ontoolapprovalchange
	}: Props = $props();

	let editorEl: HTMLDivElement | undefined = $state();
	let editor: Editor | null = $state(null);
	let voiceListening = $state(false);
	let voiceWaitingForResponse = $state(false);
	let voiceSawStreaming = $state(false);
	let voiceRecognition: any = null;
	let voiceRestartTimer = 0;
	let voiceStopRequested = false;
	let voiceRearming = $state(false);
	let voiceCaptureRecorder: MediaRecorder | null = null;
	let voiceCaptureStream: MediaStream | null = null;
	let voiceCaptureChunks: Blob[] = [];
	let voiceCaptureMimeType = 'audio/webm';
	let selectedSlashCommandIndex = $state(0);
	const voiceModeAvailable = $derived(
		$ttsEnabled && $ttsConfigured && ($voiceModeSttMode === 'browser' || $sttConfigured)
	);
	const voiceStatusLabel = $derived(
		voiceWaitingForResponse || streaming || sending
			? $t('chat.voiceWaiting')
			: voiceListening || voiceRearming || voiceModeEnabled
				? $t('chat.voiceListening')
				: $t('chat.voiceModeOn')
	);

	function isMobileInput(): boolean {
		return (
			typeof window !== 'undefined' &&
			window.matchMedia('(hover: none) and (pointer: coarse)').matches
		);
	}

	// ── Lowlight setup ──────────────────────────────
	const lowlight = createLowlight(all);
	const _origHighlight = lowlight.highlight.bind(lowlight);
	lowlight.highlight = (lang: string, value: string, opts?: Record<string, unknown>) => {
		if (!lowlight.registered(lang)) return lowlight.highlightAuto(value);
		return _origHighlight(lang, value, opts);
	};

	// ── File Uploads ────────────────────────────────
	let attachedUploads = $state<
		{ id: string; name: string; url: string; type: string; loading?: boolean }[]
	>([]);
	let isDragging = $state(false);

	function isTabDrag(e: DragEvent): boolean {
		return Boolean(
			e.dataTransfer?.types.includes(TAB_DRAG_MIME) || e.dataTransfer?.types.includes('text/tab-id')
		);
	}

	async function processFiles(files: File[]) {
		for (const file of files) {
			const id = Math.random().toString(36).substring(7);
			const isImage = file.type.startsWith('image/');
			const type = isImage ? 'image' : 'file';
			attachedUploads = [...attachedUploads, { id, name: file.name, url: '', type, loading: true }];

			try {
				const form = new FormData();
				form.append('file', file);
				const res = await uploadFile(form);
				if (res && res.id) {
					attachedUploads = attachedUploads.map((u) =>
						u.id === id ? { ...u, id: res.id, url: res.url, loading: false } : u
					);
				} else {
					attachedUploads = attachedUploads.filter((u) => u.id !== id);
				}
			} catch (err) {
				console.error('Upload failed', err);
				attachedUploads = attachedUploads.filter((u) => u.id !== id);
			}
		}
	}

	function handleDrop(e: DragEvent) {
		if (isTabDrag(e)) return;

		e.preventDefault();
		isDragging = false;
		if (e.dataTransfer?.files) {
			processFiles(Array.from(e.dataTransfer.files));
		}
	}

	function handlePaste(e: ClipboardEvent) {
		if (e.clipboardData?.items) {
			const files: File[] = [];
			for (const item of Array.from(e.clipboardData.items)) {
				if (item.kind === 'file') {
					const file = item.getAsFile();
					if (file) files.push(file);
				}
			}
			if (files.length > 0) {
				e.preventDefault(); // Stop TipTap from inserting base64 strings
				processFiles(files);
			}
		}
	}

	function removeUpload(id: string) {
		attachedUploads = attachedUploads.filter((u) => u.id !== id);
	}

	// ── @file mention suggestion ────────────────────
	let popupEl: HTMLDivElement | null = null;
	let popupComponent: Record<string, any> | null = null;
	let activeClientRectFn: (() => DOMRect | null) | null = null;
	let repositionRafId: number | null = null;

	async function fetchSuggestions({ query }: { query: string }): Promise<FileMentionAttrs[]> {
		if (!workspace) return [];
		try {
			const data = await searchFiles(query || '', workspace);
			const results = (data as any).results ?? [];
			return results.slice(0, 10).map((r: any) => ({
				id: r.type === 'directory' && !r.path.endsWith('/') ? r.path + '/' : r.path,
				label: r.name,
				type: r.type === 'directory' ? 'directory' : 'file'
			}));
		} catch {
			return [];
		}
	}

	function mountPopup(
		items: FileMentionAttrs[],
		selectedIdx: number,
		onselect: (i: number) => void
	) {
		// Destroy previous instance
		if (popupComponent) {
			try {
				unmount(popupComponent);
			} catch {}
			popupComponent = null;
		}
		if (!popupEl) {
			popupEl = document.createElement('div');
			document.body.appendChild(popupEl);
		}
		popupComponent = mount(FileSuggestionPopup, {
			target: popupEl,
			props: { items, selectedIndex: selectedIdx, onselect }
		});
	}

	function startRepositionLoop() {
		stopRepositionLoop();
		function tick() {
			if (activeClientRectFn) {
				updatePopupPosition(activeClientRectFn());
				repositionRafId = requestAnimationFrame(tick);
			}
		}
		repositionRafId = requestAnimationFrame(tick);
	}

	function stopRepositionLoop() {
		if (repositionRafId !== null) {
			cancelAnimationFrame(repositionRafId);
			repositionRafId = null;
		}
	}

	function createSuggestionRenderer() {
		let selectedIndex = 0;
		let currentItems: FileMentionAttrs[] = [];
		let command: ((attrs: FileMentionAttrs) => void) | null = null;

		function doSelect(index: number) {
			const item = currentItems[index];
			if (item && command) command(item);
		}

		function remount() {
			mountPopup(currentItems, selectedIndex, doSelect);
		}

		return {
			onStart(props: any) {
				command = props.command;
				currentItems = props.items;
				selectedIndex = 0;
				activeClientRectFn = props.clientRect ?? null;
				remount();
				updatePopupPosition(props.clientRect?.());
				startRepositionLoop();
			},
			onUpdate(props: any) {
				command = props.command;
				currentItems = props.items;
				selectedIndex = 0;
				activeClientRectFn = props.clientRect ?? null;
				remount();
				updatePopupPosition(props.clientRect?.());
			},
			onKeyDown({ event }: { event: KeyboardEvent }) {
				if (event.key === 'ArrowDown') {
					selectedIndex = (selectedIndex + 1) % Math.max(currentItems.length, 1);
					remount();
					return true;
				}
				if (event.key === 'ArrowUp') {
					selectedIndex =
						(selectedIndex - 1 + currentItems.length) % Math.max(currentItems.length, 1);
					remount();
					return true;
				}
				if (event.key === 'Enter') {
					if (isMobileInput()) return false;
					const item = currentItems[selectedIndex];
					if (item && command) command(item);
					return true;
				}
				if (event.key === 'Escape') {
					destroyPopup();
					return true;
				}
				return false;
			},
			onExit() {
				destroyPopup();
			}
		};
	}

	function updatePopupPosition(rect: DOMRect | null) {
		if (!popupEl || !rect) return;
		const child = popupEl.firstElementChild as HTMLElement | null;
		if (!child) return;
		const popupHeight = child.offsetHeight || 200;
		child.style.position = 'fixed';
		child.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 340))}px`;
		child.style.top = `${rect.top - popupHeight - 8}px`;
	}

	function destroyPopup() {
		stopRepositionLoop();
		activeClientRectFn = null;
		if (popupComponent) {
			try {
				unmount(popupComponent);
			} catch {}
			popupComponent = null;
		}
		if (popupEl) {
			popupEl.remove();
			popupEl = null;
		}
	}

	// ── $skill mention suggestion ──────────────────────
	let skillPopupEl: HTMLDivElement | null = null;
	let skillPopupComponent: Record<string, any> | null = null;
	let skillActiveClientRectFn: (() => DOMRect | null) | null = null;
	let skillRepositionRafId: number | null = null;
	let cachedSkills: SkillMentionAttrs[] | null = null;
	let cachedSkillsWorkspace = '';
	let slashSkillSuggestions = $state<SkillMentionAttrs[]>([]);
	let slashSkillsRequestId = 0;

	async function getCachedSkills(): Promise<SkillMentionAttrs[]> {
		if (!cachedSkills || cachedSkillsWorkspace !== workspace) {
			const data = await getSkills(workspace);
			cachedSkills = data.map((s) => ({
				id: s.name,
				label: s.name,
				description: s.description,
				source: s.source
			}));
			cachedSkillsWorkspace = workspace;
		}
		return cachedSkills;
	}

	async function fetchSkillSuggestions({ query }: { query: string }): Promise<SkillMentionAttrs[]> {
		try {
			const skills = await getCachedSkills();
			if (!query) return skills;
			const q = query.toLowerCase();
			return skills.filter(
				(s) => s.label.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q)
			);
		} catch {
			return [];
		}
	}

	function mountSkillPopup(
		items: SkillMentionAttrs[],
		selectedIdx: number,
		onselect: (i: number) => void
	) {
		if (skillPopupComponent) {
			try {
				unmount(skillPopupComponent);
			} catch {}
			skillPopupComponent = null;
		}
		if (!skillPopupEl) {
			skillPopupEl = document.createElement('div');
			document.body.appendChild(skillPopupEl);
		}
		skillPopupComponent = mount(SkillSuggestionPopup, {
			target: skillPopupEl,
			props: { items, selectedIndex: selectedIdx, onselect }
		});
	}

	function startSkillRepositionLoop() {
		stopSkillRepositionLoop();
		function tick() {
			if (skillActiveClientRectFn) {
				updateSkillPopupPosition(skillActiveClientRectFn());
				skillRepositionRafId = requestAnimationFrame(tick);
			}
		}
		skillRepositionRafId = requestAnimationFrame(tick);
	}

	function stopSkillRepositionLoop() {
		if (skillRepositionRafId !== null) {
			cancelAnimationFrame(skillRepositionRafId);
			skillRepositionRafId = null;
		}
	}

	function createSkillSuggestionRenderer() {
		let selectedIndex = 0;
		let currentItems: SkillMentionAttrs[] = [];
		let command: ((attrs: SkillMentionAttrs) => void) | null = null;

		function doSelect(index: number) {
			const item = currentItems[index];
			if (item && command) command(item);
		}

		function remount() {
			mountSkillPopup(currentItems, selectedIndex, doSelect);
		}

		return {
			onStart(props: any) {
				command = props.command;
				currentItems = props.items;
				selectedIndex = 0;
				skillActiveClientRectFn = props.clientRect ?? null;
				remount();
				updateSkillPopupPosition(props.clientRect?.());
				startSkillRepositionLoop();
			},
			onUpdate(props: any) {
				command = props.command;
				currentItems = props.items;
				selectedIndex = 0;
				skillActiveClientRectFn = props.clientRect ?? null;
				remount();
				updateSkillPopupPosition(props.clientRect?.());
			},
			onKeyDown({ event }: { event: KeyboardEvent }) {
				if (event.key === 'ArrowDown') {
					selectedIndex = (selectedIndex + 1) % Math.max(currentItems.length, 1);
					remount();
					return true;
				}
				if (event.key === 'ArrowUp') {
					selectedIndex =
						(selectedIndex - 1 + currentItems.length) % Math.max(currentItems.length, 1);
					remount();
					return true;
				}
				if (event.key === 'Enter') {
					if (isMobileInput()) return false;
					const item = currentItems[selectedIndex];
					if (item && command) command(item);
					return true;
				}
				if (event.key === 'Escape') {
					destroySkillPopup();
					return true;
				}
				return false;
			},
			onExit() {
				destroySkillPopup();
			}
		};
	}

	function updateSkillPopupPosition(rect: DOMRect | null) {
		if (!skillPopupEl || !rect) return;
		const child = skillPopupEl.firstElementChild as HTMLElement | null;
		if (!child) return;
		const popupHeight = child.offsetHeight || 200;
		child.style.position = 'fixed';
		child.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 280))}px`;
		child.style.top = `${rect.top - popupHeight - 8}px`;
	}

	function destroySkillPopup() {
		stopSkillRepositionLoop();
		skillActiveClientRectFn = null;
		if (skillPopupComponent) {
			try {
				unmount(skillPopupComponent);
			} catch {}
			skillPopupComponent = null;
		}
		if (skillPopupEl) {
			skillPopupEl.remove();
			skillPopupEl = null;
		}
	}

	// ── Editor lifecycle ────────────────────────────
	onMount(() => {
		if (!editorEl) return;

		const fileMention = createFileMention({
			items: fetchSuggestions,
			render: createSuggestionRenderer
		});

		const skillMention = createSkillMention({
			items: fetchSkillSuggestions,
			render: createSkillSuggestionRenderer
		});

		editor = new Editor({
			element: editorEl,
			extensions: [
				StarterKit.configure({
					codeBlock: false,
					heading: { levels: [1, 2, 3] }
				}),
				Markdown,
				Placeholder.configure({ placeholder }),
				CodeBlockLowlight.configure({ lowlight }),
				fileMention,
				skillMention
			],
			content: inputText || '',
			contentType: inputText ? 'markdown' : undefined,
			autofocus: true,
			editorProps: {
				attributes: {
					class: 'chat-prosemirror',
					spellcheck: 'true'
				},
				handleKeyDown: (view, event) => {
					if (event.key === 'Enter' && isMobileInput()) return false;

					if (showSlashCommands) {
						if (event.key === 'ArrowDown') {
							event.preventDefault();
							selectedSlashCommandIndex =
								(selectedSlashCommandIndex + 1) % Math.max(slashSuggestionIds.length, 1);
							return true;
						}
						if (event.key === 'ArrowUp') {
							event.preventDefault();
							selectedSlashCommandIndex =
								(selectedSlashCommandIndex - 1 + slashSuggestionIds.length) %
								Math.max(slashSuggestionIds.length, 1);
							return true;
						}
						if (event.key === 'Enter') {
							event.preventDefault();
							runSlashSuggestion(slashSuggestionIds[selectedSlashCommandIndex]);
							return true;
						}
					}
					if (event.key === 'Enter' && !event.shiftKey) {
						// Don't send while suggestion popup is open — let it confirm selection
						if (popupComponent || skillPopupComponent) return false;
						const { state } = view;
						const head = state.selection.$head;

						let inside = false;
						for (let d = head.depth; d > 0; d--) {
							const name = head.node(d).type.name;
							if (['codeBlock', 'bulletList', 'orderedList', 'listItem'].includes(name)) {
								inside = true;
								break;
							}
						}
						if (!inside) {
							event.preventDefault();
							handleSubmit();
							return true;
						}
					}
					return false;
				}
			},
			onUpdate: ({ editor: e }) => {
				inputText = (e as any).getMarkdown() || '';
			}
		});
	});

	onDestroy(() => {
		stopVoiceRecognition();
		if (voiceRestartTimer) clearTimeout(voiceRestartTimer);
		releaseVoiceWakeLock();
		destroyPopup();
		destroySkillPopup();
		editor?.destroy();
		editor = null;
	});

	// Sync external inputText changes (e.g. cleared after send)
	$effect(() => {
		if (!editor || editor.isDestroyed) return;
		const editorMd = (editor as any).getMarkdown() || '';
		if (inputText !== editorMd) {
			if (inputText === '') {
				editor.commands.clearContent();
			} else {
				editor.commands.setContent(inputText);
			}
		}
	});

	export function focus() {
		editor?.commands.focus();
	}

	export function resetHeight() {
		// TipTap auto-sizes; no-op kept for API compat
	}

	export function getFiles(): any[] {
		return attachedUploads.filter((u) => !u.loading);
	}

	export function clearUploads() {
		attachedUploads = [];
	}

	export function getSkillIds(): string[] {
		if (!editor) return [];
		return extractMentionedSkills(editor.getJSON());
	}

	function clearVoiceRestartTimer(resetRearming = true) {
		if (voiceRestartTimer) {
			clearTimeout(voiceRestartTimer);
			voiceRestartTimer = 0;
		}
		if (resetRearming) voiceRearming = false;
	}

	function scheduleVoiceRestart(delay = 350) {
		clearVoiceRestartTimer();
		if (
			!voiceModeAvailable ||
			!voiceModeEnabled ||
			voiceWaitingForResponse ||
			voiceListening ||
			voiceRecognition ||
			streaming ||
			sending ||
			inputText.trim()
		)
			return;
		voiceRearming = true;
		voiceRestartTimer = window.setTimeout(() => {
			voiceRestartTimer = 0;
			voiceRearming = false;
			startVoiceRecognition();
		}, delay);
	}

	function stopVoiceRecognition(stopCapture = true) {
		clearVoiceRestartTimer();
		const recognition = voiceRecognition;
		voiceRecognition = null;
		voiceStopRequested = true;
		if (stopCapture) stopVoiceCapture();
		try {
			recognition?.stop();
		} catch {}
		voiceListening = false;
	}

	function chooseVoiceCaptureMimeType() {
		if (typeof MediaRecorder === 'undefined') return 'audio/webm';
		if (MediaRecorder.isTypeSupported?.('audio/webm;codecs=opus')) return 'audio/webm;codecs=opus';
		if (MediaRecorder.isTypeSupported?.('audio/webm')) return 'audio/webm';
		if (MediaRecorder.isTypeSupported?.('audio/mp4')) return 'audio/mp4';
		return '';
	}

	async function startVoiceCapture() {
		if (!workspace || voiceCaptureRecorder || typeof MediaRecorder === 'undefined') return;
		try {
			const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
			if (!voiceRecognition || !voiceListening) {
				stream.getTracks().forEach((track) => track.stop());
				return;
			}
			const mimeType = chooseVoiceCaptureMimeType();
			const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
			voiceCaptureStream = stream;
			voiceCaptureRecorder = recorder;
			voiceCaptureChunks = [];
			voiceCaptureMimeType = recorder.mimeType || mimeType || 'audio/webm';
			recorder.ondataavailable = (event) => {
				if (event.data.size > 0) voiceCaptureChunks.push(event.data);
			};
			recorder.start();
		} catch {
			voiceCaptureRecorder = null;
			voiceCaptureStream = null;
			voiceCaptureChunks = [];
		}
	}

	async function stopVoiceCapture(): Promise<{
		blob: Blob;
		filename: string;
		contentType: string;
	} | null> {
		const recorder = voiceCaptureRecorder;
		const stream = voiceCaptureStream;
		const chunks = voiceCaptureChunks;
		const contentType = voiceCaptureMimeType || 'audio/webm';
		voiceCaptureRecorder = null;
		voiceCaptureStream = null;

		if (!recorder) {
			voiceCaptureChunks = [];
			stream?.getTracks().forEach((track) => track.stop());
			return null;
		}

		await new Promise<void>((resolve) => {
			recorder.onstop = () => resolve();
			try {
				if (recorder.state !== 'inactive') recorder.stop();
				else resolve();
			} catch {
				resolve();
			}
		});
		stream?.getTracks().forEach((track) => track.stop());
		voiceCaptureChunks = [];
		if (!chunks.length) return null;
		const ext = contentType.includes('mp4') ? 'm4a' : 'webm';
		return {
			blob: new Blob(chunks, { type: contentType }),
			filename: `voice-mode.${ext}`,
			contentType
		};
	}

	async function saveVoiceModeSttCapture(
		capture: { blob: Blob; filename: string; contentType: string },
		text: string
	) {
		// Browser STT does not need provider transcription, but the captured audio still
		// matters. Sending audio plus transcript through the normal transcribe route keeps
		// browser and provider voice samples in one STT cache for the data flywheel.
		if (!workspace || !text.trim()) return;
		const form = new FormData();
		form.append('file', capture.blob, capture.filename);
		form.append('text', text.trim());
		form.append('workspace', workspace);
		form.append('source', 'voice_mode');
		form.append('language', navigator.language || 'en-US');
		try {
			await fetch('/api/audio/transcribe', { method: 'POST', body: form });
		} catch {}
	}

	async function transcribeVoiceModeCapture(capture: {
		blob: Blob;
		filename: string;
		contentType: string;
	}): Promise<string> {
		const form = new FormData();
		form.append('file', capture.blob, capture.filename);
		form.append('workspace', workspace);
		const res = await fetch('/api/audio/transcribe', { method: 'POST', body: form });
		if (!res.ok) {
			let detail = `${res.status}`;
			try {
				const data = await res.json();
				detail = data?.detail || data?.error || detail;
			} catch {}
			throw new Error(detail);
		}
		const data = await res.json();
		return (data?.text || '').trim();
	}

	function startVoiceRecognition() {
		clearVoiceRestartTimer(false);
		if (
			!voiceModeAvailable ||
			!voiceModeEnabled ||
			voiceWaitingForResponse ||
			voiceListening ||
			voiceRecognition ||
			streaming ||
			sending ||
			inputText.trim()
		) {
			voiceRearming = false;
			return;
		}

		const SpeechRecognition =
			(window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
		if (!SpeechRecognition) {
			alert($t('chat.dictate.unsupported'));
			voiceModeEnabled = false;
			voiceRearming = false;
			return;
		}

		const recognition = new SpeechRecognition();
		voiceRecognition = recognition;
		voiceStopRequested = false;
		recognition.continuous = true;
		recognition.interimResults = true;
		recognition.lang = navigator.language || 'en-US';

		recognition.onresult = async (event: any) => {
			let browserText = '';
			for (let i = event.resultIndex; i < event.results.length; i++) {
				const result = event.results[i];
				if (result?.isFinal) browserText += ` ${result[0]?.transcript || ''}`;
			}
			browserText = browserText.trim();
			if (!browserText) return;
			voiceWaitingForResponse = true;
			voiceSawStreaming = false;
			const capturePromise = stopVoiceCapture();
			stopVoiceRecognition(false);
			const capture = await capturePromise;
			let text = browserText;
			if ($voiceModeSttMode === 'provider' && capture) {
				try {
					text = (await transcribeVoiceModeCapture(capture)) || browserText;
				} catch (err: any) {
					const detail = err?.message ? ` ${err.message}` : '';
					toast.error(`${$t('chat.voiceProviderSttFallback')}${detail}`);
					text = browserText;
				}
			} else if ($voiceModeSttMode === 'provider') {
				toast.error($t('chat.voiceProviderSttNoAudio'));
			} else if (capture) {
				void saveVoiceModeSttCapture(capture, browserText);
			}
			if (!text.trim()) {
				voiceWaitingForResponse = false;
				scheduleVoiceRestart(500);
				return;
			}
			inputText = text;
			await tick();
			handleSubmit();
		};

		recognition.onerror = (event: any) => {
			voiceListening = false;
			if (event?.error === 'not-allowed' || event?.error === 'service-not-allowed') {
				voiceModeEnabled = false;
				stopVoiceRecognition();
			}
		};

		recognition.onend = () => {
			if (voiceRecognition !== recognition) return;
			voiceRecognition = null;
			voiceListening = false;
			stopVoiceCapture();
			if (!voiceStopRequested) scheduleVoiceRestart(1200);
			voiceStopRequested = false;
		};

		try {
			recognition.start();
			voiceListening = true;
			voiceRearming = false;
			void startVoiceCapture();
		} catch {
			voiceRecognition = null;
			voiceListening = false;
			voiceRearming = false;
			scheduleVoiceRestart(1500);
		}
	}

	function toggleVoiceMode() {
		if (!voiceModeAvailable) {
			alert($t('chat.ttsNotConfigured'));
			return;
		}
		const next = !voiceModeEnabled;
		voiceModeEnabled = next;
		onsettingschange?.();
		if (next) {
			void unlockTtsAudioPlayback();
			void acquireVoiceWakeLock();
			voiceWaitingForResponse = false;
			voiceSawStreaming = false;
			startVoiceRecognition();
		} else {
			releaseVoiceWakeLock();
			voiceWaitingForResponse = false;
			voiceSawStreaming = false;
			stopVoiceRecognition();
		}
	}

	$effect(() => {
		if (!voiceModeAvailable && voiceModeEnabled) {
			voiceModeEnabled = false;
			stopVoiceRecognition();
		}
		if (!voiceModeEnabled) return;
		if (streaming) voiceSawStreaming = true;
		if (voiceSawStreaming && !streaming && !sending) {
			voiceWaitingForResponse = false;
			voiceSawStreaming = false;
			scheduleVoiceRestart(500);
		}
	});

	$effect(() => {
		if (voiceModeEnabled && inputText.trim()) {
			stopVoiceRecognition();
		}
	});

	const slashCommandQuery = $derived(inputText.trimStart().toLowerCase());
	const slashCommandIds = $derived.by(() => {
		if (!slashCommandQuery.startsWith('/')) return [];
		const ids: string[] = [];
		if (hasChatContent && oncompact && '/compact'.startsWith(slashCommandQuery))
			ids.push('compact');
		if (onplan && '/plan'.startsWith(slashCommandQuery)) ids.push('plan');
		if (hasChatContent && onfork && '/fork'.startsWith(slashCommandQuery)) ids.push('fork');
		if (hasChatContent && onstatus && '/status'.startsWith(slashCommandQuery)) ids.push('status');
		if (
			hasChatContent &&
			onskillslist &&
			slashCommandQuery !== '/skills:list' &&
			'/skills:list'.startsWith(slashCommandQuery)
		)
			ids.push('skills:list');
		if (
			hasChatContent &&
			slashCommandQuery !== '/skills:create' &&
			'/skills:create'.startsWith(slashCommandQuery)
		)
			ids.push('skills:create');
		return ids;
	});
	const slashSuggestionIds = $derived([
		...slashCommandIds.map((id) => `command:${id}`),
		...slashSkillSuggestions.map((skill) => `skill:${skill.id}`)
	]);
	const showSlashCommands = $derived(slashSuggestionIds.length > 0);
	const contextPercent = $derived(Math.max(0, Math.round(contextUsage?.percent ?? 0)));
	const contextCirclePercent = $derived(Math.min(contextPercent, 100));
	const contextCircleOffset = $derived(50.27 * (1 - contextCirclePercent / 100));

	$effect(() => {
		if (selectedSlashCommandIndex >= slashSuggestionIds.length) selectedSlashCommandIndex = 0;
	});

	$effect(() => {
		const query = slashCommandQuery;
		const requestId = ++slashSkillsRequestId;
		if (!query.startsWith('/') || /\s/.test(query.slice(1))) {
			slashSkillSuggestions = [];
			return;
		}
		void fetchSkillSuggestions({ query: query.slice(1) }).then((items) => {
			if (requestId !== slashSkillsRequestId) return;
			slashSkillSuggestions = items.slice(0, 5);
		});
	});

	function selectedSlashCommand(commandId: string) {
		return slashSuggestionIds[selectedSlashCommandIndex] === `command:${commandId}`;
	}

	function selectSlashCommand(commandId: string) {
		selectedSlashCommandIndex = slashSuggestionIds.indexOf(`command:${commandId}`);
	}

	function selectedSlashSkill(skillId: string) {
		return slashSuggestionIds[selectedSlashCommandIndex] === `skill:${skillId}`;
	}

	function selectSlashSkill(skillId: string) {
		selectedSlashCommandIndex = slashSuggestionIds.indexOf(`skill:${skillId}`);
	}

	function runSlashSuggestion(suggestionId: string | undefined) {
		if (suggestionId?.startsWith('skill:')) {
			const skillId = suggestionId.slice('skill:'.length);
			const skill = slashSkillSuggestions.find((item) => item.id === skillId);
			runSlashSkill(skill ?? { id: skillId, label: skillId });
			return;
		}
		runSlashCommand(suggestionId?.replace(/^command:/, ''));
	}

	function runSlashSkill(skill: SkillMentionAttrs) {
		if (!editor || editor.isDestroyed) {
			inputText = `$${skill.label} `;
			return;
		}
		editor
			.chain()
			.focus()
			.clearContent()
			.insertContent([
				{
					type: 'skillMention',
					attrs: {
						id: skill.id,
						label: skill.label,
						description: skill.description,
						source: skill.source
					}
				},
				{ type: 'text', text: ' ' }
			])
			.run();
	}

	function runSlashCommand(commandId: string | undefined) {
		if (commandId === 'compact' && (sending || streaming)) return;
		if (commandId === 'fork' && (sending || streaming)) return;
		if (commandId === 'compact' && oncompact) {
			inputText = '';
			oncompact();
			return;
		}
		if (commandId === 'fork' && onfork) {
			inputText = '';
			onfork();
			return;
		}
		if (commandId === 'plan' && onplan) {
			inputText = '';
			onplan();
			return;
		}
		if (commandId === 'status' && onstatus) {
			inputText = '';
			onstatus();
			return;
		}
		if (commandId === 'skills:list' && onskillslist) {
			inputText = '';
			onskillslist();
			return;
		}
		if (commandId === 'skills:create') {
			inputText = '/skills:create ';
			return;
		}
		onsend();
	}

	function handleSubmit() {
		runSlashSuggestion(slashSuggestionIds[0]);
	}

	// Allow sending during streaming (message will be enqueued server-side)
	const canSend = $derived(!!(inputText.trim() && selectedModel && !sending));
</script>

<div
	class="relative {isDragging ? 'ring-2 ring-blue-500 rounded-3xl' : ''}"
	ondrop={handleDrop}
	onpaste={handlePaste}
	ondragover={(e) => {
		if (isTabDrag(e)) {
			isDragging = false;
			return;
		}
		e.preventDefault();
		isDragging = true;
	}}
	ondragleave={() => {
		isDragging = false;
	}}
	role="presentation"
>
	{#if askUser && onaskuseranswer}
		<div class="mx-1">
			<AskUserCard
				item={askUser.item}
				pairedOutput={askUser.output}
				chatId={askUser.chatId}
				messageId={askUser.messageId}
				onanswer={onaskuseranswer}
			/>
		</div>
	{/if}

	{#if tasks.length > 0}
		<div class="mx-1">
			<Tasks {tasks} />
		</div>
	{/if}

	<!-- Queued messages (above input, matching open-webui layout) -->
	{#if queuedMessages.length > 0}
		<div
			class="app-subtle-surface mb-1 mx-2 py-0.5 px-1.5 rounded-2xl border overflow-x-hidden overflow-y-auto max-h-[25vh]"
		>
			{#each queuedMessages as qm (qm.id)}
				<QueuedMessageItem
					id={qm.id}
					content={qm.content}
					onsendnow={onqueuesendnow ?? (() => {})}
					onedit={onqueueedit ?? (() => {})}
					ondelete={onqueuedelete ?? (() => {})}
				/>
			{/each}
		</div>
	{/if}

	{#if showSlashCommands}
		<div
			class="app-theme app-surface absolute left-2 bottom-full mb-1 z-50 w-64 max-h-40 overflow-y-auto rounded-xl border shadow-xl p-0.5"
		>
			{#if slashCommandIds.length > 0}
				<div class="app-muted mb-0.5 px-2 pt-1 pb-0.5 text-[0.625rem] leading-none">
					{$t('chat.commands')}
				</div>
			{/if}
			{#if slashCommandIds.includes('compact')}
				<button
					type="button"
					aria-label={`${$t('chat.commandCompact')}: ${$t('chat.commandCompactDesc')}`}
					use:tooltip={{
						content: $t('chat.commandCompactDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('compact') ? 'app-interactive-active' : ''} disabled:opacity-50"
					disabled={sending || streaming}
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('compact');
					}}
					onmouseenter={() => selectSlashCommand('compact')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<svg class="size-3.5 -rotate-90" viewBox="0 0 20 20" aria-hidden="true">
							<circle
								cx="10"
								cy="10"
								r="8"
								fill="none"
								stroke="currentColor"
								stroke-width="2"
								class="opacity-20"
							/>
							<circle
								cx="10"
								cy="10"
								r="8"
								fill="none"
								stroke="currentColor"
								stroke-width="2"
								stroke-linecap="round"
								stroke-dasharray="50.27"
								style={`stroke-dashoffset: ${contextCircleOffset};`}
							/>
						</svg>
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandCompact')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">
							{$t('chat.commandCompactPercent', { percent: contextPercent })}
						</span>
					</span>
				</button>
			{/if}
			{#if slashCommandIds.includes('plan')}
				<button
					type="button"
					aria-label={`${$t('chat.commandPlan')}: ${$t('chat.commandPlanDesc')}`}
					use:tooltip={{
						content: $t('chat.commandPlanDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('plan') ? 'app-interactive-active' : ''}"
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('plan');
					}}
					onmouseenter={() => selectSlashCommand('plan')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<svg
							class="size-3.5"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.75"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
							<polyline points="14 2 14 8 20 8" />
							<line x1="9" y1="13" x2="15" y2="13" />
							<line x1="9" y1="17" x2="15" y2="17" />
						</svg>
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandPlan')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">
							{planMode ? $t('chat.commandPlanOn') : $t('chat.commandPlanOff')}
						</span>
					</span>
				</button>
			{/if}
			{#if slashCommandIds.includes('fork')}
				<button
					type="button"
					aria-label={`${$t('chat.commandFork')}: ${$t('chat.commandForkDesc')}`}
					use:tooltip={{
						content: $t('chat.commandForkDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('fork') ? 'app-interactive-active' : ''} disabled:opacity-50"
					disabled={sending || streaming}
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('fork');
					}}
					onmouseenter={() => selectSlashCommand('fork')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<Icon name="chat-fork" size={14} strokeWidth={1.8} />
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandFork')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">
							{$t('chat.commandForkDesc')}
						</span>
					</span>
				</button>
			{/if}
			{#if slashCommandIds.includes('status')}
				<button
					type="button"
					aria-label={`${$t('chat.commandStatus')}: ${$t('chat.commandStatusDesc')}`}
					use:tooltip={{
						content: $t('chat.commandStatusDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('status') ? 'app-interactive-active' : ''}"
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('status');
					}}
					onmouseenter={() => selectSlashCommand('status')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<svg
							class="size-3.5"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.75"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M12 14l4-4" />
							<path d="M3.34 19a10 10 0 1 1 17.32 0" />
						</svg>
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandStatus')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">
							{$t('chat.commandStatusDesc')}
						</span>
					</span>
				</button>
			{/if}
			{#if slashCommandIds.includes('skills:list')}
				<button
					type="button"
					aria-label={`${$t('chat.commandListSkills')}: ${$t('chat.commandListSkillsDesc')}`}
					use:tooltip={{
						content: $t('chat.commandListSkillsDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('skills:list') ? 'app-interactive-active' : ''}"
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('skills:list');
					}}
					onmouseenter={() => selectSlashCommand('skills:list')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<Icon name="list" size={14} />
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandListSkills')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">/skills:list</span>
					</span>
				</button>
			{/if}
			{#if slashCommandIds.includes('skills:create')}
				<button
					type="button"
					aria-label={`${$t('chat.commandCreateSkill')}: ${$t('chat.commandCreateSkillDesc')}`}
					use:tooltip={{
						content: $t('chat.commandCreateSkillDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('skills:create') ? 'app-interactive-active' : ''}"
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('skills:create');
					}}
					onmouseenter={() => selectSlashCommand('skills:create')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<Icon name="plus" size={14} />
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandCreateSkill')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">/skills:create</span>
					</span>
				</button>
			{/if}
			{#if slashSkillSuggestions.length > 0}
				<div class="app-muted mb-0.5 px-2 pt-1 pb-0.5 text-[0.625rem] leading-none">
					{$t('chat.skills')}
				</div>
				{#each slashSkillSuggestions as skill (skill.id)}
					<button
						type="button"
						aria-label={`${$t('chat.useSkill')}: ${skill.label}`}
						use:tooltip={{
							content: skill.label,
							placement: 'top'
						}}
						class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
							{selectedSlashSkill(skill.id) ? 'app-interactive-active' : ''}"
						onmousedown={(e) => e.preventDefault()}
						onclick={() => {
							runSlashSkill(skill);
						}}
						onmouseenter={() => selectSlashSkill(skill.id)}
					>
						<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
							<Icon name="spark" size={13} strokeWidth={1.7} />
						</span>
						<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
							<span class="truncate">{skill.label}</span>
							{#if skill.source && skill.source !== 'workspace'}
								<span class="app-muted text-[0.625rem] truncate shrink-0">{skill.source}</span>
							{/if}
						</span>
					</button>
				{/each}
			{/if}
		</div>
	{/if}

	<div class="app-surface rounded-3xl shadow-lg border transition px-1">
		<!-- Uploaded Files Preview -->
		{#if attachedUploads.length > 0}
			<div class="mx-2 pt-2 flex flex-wrap gap-2">
				{#each attachedUploads as upload}
					<div class="relative group flex-shrink-0">
						{#if upload.loading}
							<div
								class="app-subtle-surface flex items-center justify-center size-8 rounded-xl border shadow-sm"
							>
								<Spinner size={16} />
							</div>
						{:else if upload.type === 'image'}
							<img
								src={upload.url}
								alt={upload.name}
								class="app-surface size-8 object-cover rounded-xl border shadow-sm"
							/>
						{:else}
							<div
								class="app-surface relative group px-2 w-48 h-8 flex items-center gap-1.5 border rounded-xl text-left flex-shrink-0 shadow-sm"
							>
								<div class="shrink-0">
									<Icon name="page-text" size={14} class="app-icon-muted" />
								</div>
								<div class="flex flex-col justify-center w-full overflow-hidden">
									<div class="text-xs flex justify-between items-center w-full gap-2">
										<div class="font-medium truncate flex-1">{upload.name}</div>
										<div class="app-muted text-[0.625rem] capitalize shrink-0">
											{upload.type === 'file' ? 'File' : upload.type}
										</div>
									</div>
								</div>
							</div>
						{/if}

						<div class="absolute -top-1.5 -right-1.5 z-10">
							<button
								class="app-surface border rounded-full group-hover:visible invisible transition outline-none shadow-sm flex items-center justify-center"
								type="button"
								onclick={() => removeUpload(upload.id)}
								aria-label={$t('chat.removeUpload')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="size-3.5"
									aria-hidden="true"
								>
									<path
										d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
									/>
								</svg>
							</button>
						</div>
					</div>
				{/each}
			</div>
		{/if}
		<!-- Editor area -->
		<div class="px-2.5">
			{#if voiceModeEnabled}
				<div class="app-muted pt-2 flex items-center gap-2 text-[0.6875rem] font-medium">
					<span class="relative flex size-2">
						<span
							class="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-70 {voiceListening
								? 'animate-ping'
								: ''}"
						></span>
						<span class="relative inline-flex size-2 rounded-full bg-emerald-500"></span>
					</span>
					<span>{voiceStatusLabel}</span>
				</div>
			{/if}
			<div
				bind:this={editorEl}
				class="chat-editor-mount scrollbar-hidden"
				class:opacity-50={sending}
				class:pointer-events-none={sending}
			></div>
		</div>

		<!-- Toolbar. stopPropagation prevents TipTap from stealing focus -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="flex items-center justify-between mt-0.5 mb-2.5 mx-0.5"
			onmousedown={(e) => e.stopPropagation()}
		>
			<div class="ml-0.5 self-end flex items-center gap-1">
				<PlusMenu
					bind:toolApprovalMode
					bind:planMode
					bind:requestParams
					onchange={onsettingschange}
					{ontoolapprovalchange}
					onfiles={(files) => {
						if (files) processFiles(Array.from(files));
					}}
					oncapture={(file) => {
						processFiles([file]);
					}}
				/>
				{#if planMode}
					<button
						type="button"
						aria-label={$t('chat.commandPlanOff')}
						title={$t('chat.commandPlanOff')}
						class="app-surface app-interactive group p-[0.3125rem] flex gap-1 items-center text-xs rounded-full transition-colors duration-150 border"
						onclick={() => onplan?.()}
					>
						<svg
							class="size-3 shrink-0 group-hover:hidden"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.75"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
							<polyline points="14 2 14 8 20 8" />
							<line x1="9" y1="13" x2="15" y2="13" />
							<line x1="9" y1="17" x2="15" y2="17" />
						</svg>
						<svg
							class="size-3 shrink-0 hidden group-hover:block"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							<line x1="18" y1="6" x2="6" y2="18" />
							<line x1="6" y1="6" x2="18" y2="18" />
						</svg>
					</button>
				{/if}
			</div>
			<div class="self-end mr-1 flex items-center gap-2">
				<ModelSelector bind:selectedModel onchange={onsettingschange} />
				<DictateButton
					ontext={(text) => {
						inputText += text;
					}}
				/>
				<SendButton
					{canSend}
					{streaming}
					onsend={handleSubmit}
					{oncancel}
					onvoice={voiceModeAvailable ? toggleVoiceMode : undefined}
					voiceActive={voiceModeEnabled || voiceListening}
				/>
			</div>
		</div>
	</div>
</div>

<style>
	@reference "../../../app.css";

	/* ── ProseMirror editor ───────────────────────── */

	.chat-editor-mount :global(.chat-prosemirror) {
		@apply pt-2.5 pb-2 px-1 min-h-6 max-h-96 overflow-y-auto text-[0.8125rem] leading-relaxed outline-none break-words;
		font-size: 0.8125rem;
		color: var(--app-fg);
	}

	/* Placeholder */
	.chat-editor-mount :global(.chat-prosemirror p.is-editor-empty:first-child::before) {
		content: attr(data-placeholder);
		@apply float-left pointer-events-none h-0;
		color: color-mix(in oklab, var(--app-fg) 48%, var(--app-bg));
	}

	/* Paragraphs */
	.chat-editor-mount :global(.chat-prosemirror p) {
		@apply mb-1;
	}
	.chat-editor-mount :global(.chat-prosemirror p:last-child) {
		@apply mb-0;
	}

	/* Lists */
	.chat-editor-mount :global(.chat-prosemirror ul),
	.chat-editor-mount :global(.chat-prosemirror ol) {
		@apply mb-1 pl-4.5 text-sm;
	}
	.chat-editor-mount :global(.chat-prosemirror li) {
		@apply my-0.5;
	}
	.chat-editor-mount :global(.chat-prosemirror li > p) {
		@apply mb-0.5;
	}

	/* Inline code */
	.chat-editor-mount :global(.chat-prosemirror code) {
		@apply rounded-sm px-1 py-px text-xs font-mono;
		background: color-mix(in oklab, var(--app-fg) 7%, transparent);
	}

	/* Code blocks */
	.chat-editor-mount :global(.chat-prosemirror pre) {
		@apply rounded-md px-3 py-2 overflow-x-auto my-1 text-xs font-mono;
		background: color-mix(in oklab, var(--app-fg) 5%, transparent);
	}
	.chat-editor-mount :global(.chat-prosemirror pre code) {
		@apply bg-transparent p-0 rounded-none text-inherit;
	}

	/* Blockquote */
	.chat-editor-mount :global(.chat-prosemirror blockquote) {
		@apply my-1 py-0.5 pl-3 border-l-2;
		border-color: color-mix(in oklab, var(--app-fg) 18%, transparent);
		color: color-mix(in oklab, var(--app-fg) 62%, var(--app-bg));
	}

	/* Strong */
	.chat-editor-mount :global(.chat-prosemirror strong) {
		@apply font-semibold;
	}

	/* Headings */
	.chat-editor-mount :global(.chat-prosemirror h1) {
		@apply text-base font-semibold my-1;
	}
	.chat-editor-mount :global(.chat-prosemirror h2) {
		@apply text-sm font-semibold my-1;
	}
	.chat-editor-mount :global(.chat-prosemirror h3) {
		@apply text-[0.8125rem] font-semibold my-1;
		font-size: 0.8125rem;
	}

	/* HR */
	.chat-editor-mount :global(.chat-prosemirror hr) {
		@apply border-none border-t my-2;
		border-color: color-mix(in oklab, var(--app-fg) 10%, transparent);
	}

	/* Syntax highlighting */
	.chat-editor-mount :global(.hljs-keyword) {
		color: #c678dd;
	}
	.chat-editor-mount :global(.hljs-string) {
		color: #98c379;
	}
	.chat-editor-mount :global(.hljs-number) {
		color: #d19a66;
	}
	.chat-editor-mount :global(.hljs-comment) {
		color: #5c6370;
		font-style: italic;
	}
	.chat-editor-mount :global(.hljs-function) {
		color: #61afef;
	}
	.chat-editor-mount :global(.hljs-title) {
		color: #61afef;
	}
	.chat-editor-mount :global(.hljs-built_in) {
		color: #e5c07b;
	}
</style>
