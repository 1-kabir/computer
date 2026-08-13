<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import {
		currentWorkspace,
		workspaceList,
		addWorkspace,
		loadWorkspace,
		gitReviewOpen,
		setActiveGroup,
		setSplitRatio,
		moveTabToNewSplit,
		openChatTab,
		openFileTab,
		openTerminalTab,
		setFileBrowserCwd,
		appVersion,
		showChangelog,
		showSearch,
		pwaPreferences,
		homeState,
		splitHomeTab,
		closeHomeGroup,
		setHomeActiveGroup,
		moveHomeTabToNewSplit,
		moveHomeTabToGroup,
		setHomeSplitRatio
	} from '$lib/stores';
	import type { Tab, EditorGroup, EditorLayout, SplitDirection, WorkspaceState } from '$lib/stores';
	import { chatEnabled } from '$lib/stores/chat';
	import { t } from '$lib/i18n';
	import { session } from '$lib/session';
	import { get } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import { getWelcome, getWorkspaceState } from '$lib/apis/state';
	import { createSession, deleteSession } from '$lib/apis/terminal';
	import { createBrowserSession, deleteBrowserSession } from '$lib/apis/browser';
	import { createEntry, writeFile, uploadFiles as uploadFilesApi } from '$lib/apis/files';
	import { getChat, getChats, type ChatInfo } from '$lib/apis/chat';
	import { deleteSharePayload, getSharePayload } from '$lib/intents/payloadStore';
	import type { LaunchIntent, ShareBehavior, SharePayload } from '$lib/intents/types';
	import FileBrowser from '$lib/components/FileBrowser.svelte';
	import FileEditor from '$lib/components/FileEditor.svelte';
	import GitView from '$lib/components/GitView.svelte';
	import Terminal from '$lib/components/Terminal.svelte';
	import BrowserPreview from '$lib/components/BrowserPreview.svelte';
	import ChatPanel from '$lib/components/chat/ChatPanel.svelte';
	import DirectoryPicker from '$lib/components/DirectoryPicker.svelte';
	import GroupTabBar from '$lib/components/GroupTabBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import WorkspacePicker from '$lib/components/WorkspacePicker.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { TAB_DRAG_MIME } from '$lib/constants';
	import { isSupportedWorkspacePath } from '$lib/utils/paths';

	let showPicker = $state(false);
	let pendingIntent = $state<LaunchIntent | null>(null);
	let folderPickerIntent = $state<LaunchIntent | null>(null);
	let folderPickerWorkspace = $state<string | null>(null);
	const welcomeName = $derived($session?.display_name || $session?.username);
	const greetingTime = $derived.by(() => {
		const hour = new Date().getHours();
		if (hour < 5) return 'lateNight';
		if (hour < 7) return 'superEarly';
		if (hour < 11) return 'morning';
		if (hour < 13) return 'noon';
		if (hour < 17) return 'afternoon';
		if (hour < 21) return 'evening';
		return 'night';
	});
	const greetingVariant = $derived(new Date().getDate() % 3);
	const greetingNameMarker = '\uE000';
	const activeHomeGroup = $derived(
		$homeState.groups.find((group) => group.id === $homeState.activeGroupId) ?? $homeState.groups[0]
	);
	function updateHomeTabs(
		groupId: string,
		update: (tabs: Tab[]) => { tabs: Tab[]; activeTabId: string }
	) {
		homeState.update((state) => ({
			...state,
			activeGroupId: groupId,
			groups: state.groups.map((group) =>
				group.id === groupId ? { ...group, ...update(group.tabs) } : group
			)
		}));
	}

	function reorderHomeTabs(groupId: string, oldIndex: number, newIndex: number) {
		const group = $homeState.groups.find((item) => item.id === groupId);
		if (!group) return;
		const tabs = [...group.tabs];
		const [tab] = tabs.splice(oldIndex, 1);
		if (!tab) return;
		tabs.splice(newIndex, 0, tab);
		updateHomeTabs(groupId, () => ({ tabs, activeTabId: group.activeTabId }));
	}

	function cycleHomeTab(direction: 1 | -1) {
		const group = activeHomeGroup;
		if (!group || group.tabs.length < 2) return;
		const currentIndex = group.tabs.findIndex((tab) => tab.id === group.activeTabId);
		if (currentIndex === -1) return;
		const nextIndex = (currentIndex + direction + group.tabs.length) % group.tabs.length;
		updateHomeTabs(group.id, (tabs) => ({ tabs, activeTabId: tabs[nextIndex].id }));
	}

	function toggleHomeSplit() {
		if ($homeState.groups.length > 1) {
			const group = $homeState.groups.find((item) => item.id !== $homeState.activeGroupId);
			if (group) closeHomeGroup(group.id);
		} else {
			splitHomeTab();
		}
	}

	function openHomeChat(chatId?: string, groupId = $homeState.activeGroupId) {
		const group = $homeState.groups.find((item) => item.id === groupId);
		if (!group) return;
		const existing = group.tabs.find(
			(tab) =>
				tab.type === 'chat' &&
				(chatId
					? tab.path === chatId
					: tab.path?.startsWith('new-') || tab.path?.startsWith('pending-'))
		);
		if (existing) {
			updateHomeTabs(groupId, (tabs) => ({ tabs, activeTabId: existing.id }));
			return;
		}
		const tab: Tab = {
			id: `home-${Date.now()}`,
			type: 'chat',
			label: chatId ? 'Chat' : 'New Chat',
			path: chatId || `new-${Date.now()}`
		};
		updateHomeTabs(groupId, (tabs) => ({ tabs: [...tabs, tab], activeTabId: tab.id }));
	}

	async function openHomeTerminal(groupId = $homeState.activeGroupId) {
		try {
			const session = await createSession();
			const tab: Tab = {
				id: `home-${Date.now()}`,
				type: 'terminal',
				label: 'Terminal',
				sessionId: session.session_id
			};
			updateHomeTabs(groupId, (tabs) => ({ tabs: [...tabs, tab], activeTabId: tab.id }));
		} catch (error) {
			console.error('Failed to create Home terminal:', error);
		}
	}

	async function openHomeBrowser(url?: string, groupId = $homeState.activeGroupId) {
		try {
			const session = await createBrowserSession(url);
			const tab: Tab = {
				id: `home-${Date.now()}`,
				type: 'browser',
				label: 'Browser',
				path: url,
				browserSessionId: session.session_id
			};
			updateHomeTabs(groupId, (tabs) => ({ tabs: [...tabs, tab], activeTabId: tab.id }));
		} catch (error) {
			console.error('Failed to create Home browser:', error);
			toast.error(error instanceof Error ? error.message : 'Failed to open Browser');
		}
	}

	function closeHomeTab(tabId: string, groupId = $homeState.activeGroupId) {
		const group = $homeState.groups.find((item) => item.id === groupId);
		if (!group) return;
		const index = group.tabs.findIndex((tab) => tab.id === tabId);
		const tab = group.tabs[index];
		if (!tab || tab.permanent) return;
		if (tab.type === 'terminal' && tab.sessionId) deleteSession(tab.sessionId);
		if (tab.type === 'browser' && tab.browserSessionId) deleteBrowserSession(tab.browserSessionId);
		const tabs = group.tabs.filter((item) => item.id !== tabId);
		const activeTabId =
			group.activeTabId === tabId
				? (tabs[Math.max(0, index - 1)]?.id ?? 'home')
				: group.activeTabId;
		updateHomeTabs(groupId, () => ({ tabs, activeTabId }));
		if (!tabs.length) closeHomeGroup(groupId);
	}

	function updateHomeChatTab(
		tabId: string,
		chatId: string,
		label: string,
		groupId = $homeState.activeGroupId
	) {
		const group = $homeState.groups.find((item) => item.id === groupId);
		if (!group) return;
		updateHomeTabs(groupId, (tabs) => ({
			tabs: tabs.map((tab) => (tab.id === tabId ? { ...tab, path: chatId, label } : tab)),
			activeTabId: group.activeTabId
		}));
	}

	function updateHomeBrowserTab(tabId: string, label: string, groupId = $homeState.activeGroupId) {
		const group = $homeState.groups.find((item) => item.id === groupId);
		if (!group) return;
		updateHomeTabs(groupId, (tabs) => ({
			tabs: tabs.map((tab) => (tab.id === tabId ? { ...tab, label } : tab)),
			activeTabId: group.activeTabId
		}));
	}
	const INTENT_URL_KEYS = [
		'intent',
		'chatId',
		'file',
		'dir',
		'targetDir',
		'payload',
		'shareBehavior',
		'title',
		'text',
		'url'
	];

	// ── URL-driven workspace loading ───────────────────────────────
	// The workspace path comes from the URL query param: ?workspace=/path/to/dir
	// Each browser tab has its own URL → its own workspace.
	// Intent params (chatId, file, dir) are processed AFTER loading.

	let lastLoadedPath = $state<string | null>(null);
	type LaunchQueueWindow = Window & {
		__cptrLaunchQueueBound?: boolean;
		launchQueue?: {
			setConsumer: (
				consumer: (params: { files?: { getFile: () => Promise<File> }[] }) => void
			) => void;
		};
	};

	function urlIntent(url: URL): LaunchIntent | null {
		const protocol = webCptrIntent(url.searchParams.get('intent'));
		const params = protocol?.params ?? url.searchParams;
		const intent = protocol?.intent ?? url.searchParams.get('intent');
		const workspace = params.get('workspace') || undefined;
		const chatId = params.get('chatId');
		const filePath = params.get('file');
		const dirPath = params.get('dir');

		if (intent === 'newNote') return { kind: 'newNote', workspace };
		if (intent === 'newChat') return { kind: 'newChat', workspace };
		if (intent === 'newTerminal') return { kind: 'newTerminal', workspace };
		if (intent === 'openWorkspace') return { kind: 'openWorkspace' };
		if (intent === 'search') return { kind: 'search', workspace };
		if (intent === 'importFiles') {
			return { kind: 'importFiles', workspace, targetDir: params.get('targetDir') || undefined };
		}
		if (intent === 'share') {
			return {
				kind: 'share',
				workspace,
				payloadId: params.get('payload') || undefined,
				targetDir: params.get('targetDir') || undefined,
				shareBehavior: (params.get('shareBehavior') || undefined) as ShareBehavior | undefined,
				share: {
					title: params.get('title') || undefined,
					text: params.get('text') || undefined,
					url: params.get('url') || undefined
				}
			};
		}
		if (chatId !== null) return { kind: 'openChat', workspace, chatId: chatId || null };
		if (filePath) return { kind: 'openFile', workspace, filePath };
		if (dirPath) return { kind: 'openDir', workspace, dirPath };
		return null;
	}

	function webCptrIntent(
		raw: string | null
	): { intent: string | null; params: URLSearchParams } | null {
		if (!raw) return null;
		let decoded = raw;
		try {
			decoded = decodeURIComponent(raw);
		} catch {}
		if (!decoded.startsWith('web+cptr:')) return null;
		try {
			const parsed = new URL(decoded);
			return {
				intent:
					parsed.searchParams.get('intent') ||
					parsed.hostname ||
					parsed.pathname.replace(/^\/+/, '') ||
					null,
				params: parsed.searchParams
			};
		} catch {
			const rest = decoded.replace(/^web\+cptr:(\/\/)?/, '');
			const [intentPart, query = ''] = rest.split('?');
			return { intent: intentPart.replace(/^\/+/, '') || null, params: new URLSearchParams(query) };
		}
	}

	function clearIntentUrl(url: URL): string {
		const next = new URL(url);
		for (const key of INTENT_URL_KEYS) next.searchParams.delete(key);
		return next.pathname + next.search;
	}

	function noteBaseName(now = new Date()): string {
		const pad = (n: number) => String(n).padStart(2, '0');
		return `note-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(
			now.getHours()
		)}${pad(now.getMinutes())}`;
	}

	function shareMarkdown(payload: SharePayload): string {
		const lines: string[] = [];
		if (payload.title) lines.push(`# ${payload.title.trim()}`, '');
		if (payload.text) lines.push(payload.text.trim(), '');
		if (payload.url) lines.push(payload.url.trim(), '');
		if (payload.files?.length) {
			lines.push('Files:', ...payload.files.map((file) => `- ${file.name}`), '');
		}
		return lines.join('\n').trimEnd() + '\n';
	}

	async function processIntentParams() {
		const url = new URL($page.url);
		const parsed = urlIntent(url);
		if (parsed) history.replaceState(history.state, '', clearIntentUrl(url));
		if (!parsed) return;
		await handleIntent(parsed);
	}

	async function handleIntent(intent: LaunchIntent) {
		if (intent.kind === 'openWorkspace') {
			showPicker = true;
			return;
		}

		const targetWorkspace = intent.workspace;
		const needsWorkspace = intentNeedsExplicitWorkspace(intent.kind);
		if (needsWorkspace && !targetWorkspace) {
			pendingIntent = intent;
			return;
		}

		switch (intent.kind) {
			case 'newNote':
				await createNote(targetWorkspace!);
				break;
			case 'newChat':
				if (targetWorkspace) openChatTab();
				else openHomeChat();
				break;
			case 'newTerminal':
				if (targetWorkspace) await openTerminalTab();
				else await openHomeTerminal();
				break;
			case 'search':
				showSearch.set(true);
				break;
			case 'openChat':
				if (targetWorkspace) {
					openChatTab(intent.chatId ?? undefined);
				} else if (intent.chatId) {
					const chat = await getChat(intent.chatId).catch(() => null);
					const chatWorkspace = chat?.chat.meta?.workspace;
					if (typeof chatWorkspace === 'string' && chatWorkspace) {
						addWorkspace(chatWorkspace);
						await goto(
							`/?workspace=${encodeURIComponent(chatWorkspace)}&chatId=${encodeURIComponent(intent.chatId)}`
						);
					} else {
						openHomeChat(intent.chatId);
					}
				} else {
					openHomeChat();
				}
				break;
			case 'openFile':
				if (intent.filePath) openFileTab(intent.filePath);
				break;
			case 'openDir':
				if (intent.dirPath) setFileBrowserCwd(intent.dirPath);
				break;
			case 'share':
				await handleShareIntent(intent, targetWorkspace!);
				break;
			case 'importFiles':
				await importIntentFiles(intent, targetWorkspace!);
				break;
		}
	}

	function intentNeedsExplicitWorkspace(kind: LaunchIntent['kind']): boolean {
		return ['newNote', 'openFile', 'openDir', 'share', 'importFiles'].includes(kind);
	}

	async function createNote(workspacePath: string, content = '') {
		if (get(currentWorkspace)?.path !== workspacePath) {
			await loadWorkspace(workspacePath);
		}

		const base = noteBaseName();
		for (let i = 1; i < 100; i += 1) {
			const suffix = i === 1 ? '' : `-${i}`;
			const path = `${workspacePath}/${base}${suffix}.md`;
			try {
				await createEntry(path, 'file');
				if (content) await writeFile(path, content);
				openFileTab(path, undefined, { edit: true });
				return path;
			} catch (e: any) {
				if (e?.status !== 409) throw e;
			}
		}
		throw new Error('Could not create note');
	}

	async function handleShareIntent(intent: LaunchIntent, workspacePath: string) {
		const behavior = intent.shareBehavior ?? get(pwaPreferences).shareBehavior;
		if (behavior === 'ask') {
			pendingIntent = intent;
			return;
		}

		let share = intent.share;
		if (intent.payloadId) {
			share = (await getSharePayload(intent.payloadId)) ?? share;
			await deleteSharePayload(intent.payloadId).catch(() => {});
		}
		if (!share) return;

		if (behavior === 'chatDraft') {
			sessionStorage.setItem(`cptr:intent:chatDraft:${workspacePath}`, shareMarkdown(share));
			openChatTab();
			return;
		}
		await createNote(workspacePath, shareMarkdown(share));
	}

	async function importIntentFiles(intent: LaunchIntent, workspacePath: string) {
		const files = intent.importFiles?.files;
		if (!files?.length) return;
		const prefs = get(pwaPreferences);
		if (prefs.importDestination === 'askFolder' && !intent.targetDir) {
			folderPickerIntent = intent;
			folderPickerWorkspace = workspacePath;
			showPicker = true;
			return;
		}
		const directory = resolveImportDirectory(workspacePath, intent.targetDir);
		for (const file of files) {
			const form = new FormData();
			form.append('file', file, file.name);
			form.append('directory', directory);
			await uploadFilesApi(directory, form);
		}
	}

	function resolveImportDirectory(workspacePath: string, targetDir?: string): string {
		if (targetDir) return targetDir;
		const prefs = get(pwaPreferences);
		if (prefs.importDestination !== 'configuredFolder' || !prefs.importFolder?.trim()) {
			return workspacePath;
		}
		const folder = prefs.importFolder.trim();
		if (folder.startsWith('/')) return folder;
		return `${workspacePath}/${folder.replace(/^\/+/, '')}`;
	}

	async function chooseIntentWorkspace(path: string, behavior?: ShareBehavior) {
		if (!pendingIntent) return;
		addWorkspace(path);
		const intent: LaunchIntent = {
			...pendingIntent,
			workspace: path,
			shareBehavior: behavior ?? pendingIntent.shareBehavior
		};
		pendingIntent = null;
		const params = new URLSearchParams({ workspace: path });
		await goto(`/?${params.toString()}`, { replaceState: true });
		if (get(currentWorkspace)?.path !== path) await loadWorkspace(path);
		await handleIntent(intent);
	}

	function handlePickedImportFolder(path: string) {
		if (!folderPickerIntent || !folderPickerWorkspace) return;
		const intent: LaunchIntent = {
			...folderPickerIntent,
			workspace: folderPickerWorkspace,
			targetDir: path
		};
		folderPickerIntent = null;
		folderPickerWorkspace = null;
		showPicker = false;
		void handleIntent(intent);
	}

	$effect(() => {
		const workspacePath = $page.url.searchParams.get('workspace');
		if (workspacePath && !isSupportedWorkspacePath(workspacePath)) {
			lastLoadedPath = null;
			currentWorkspace.set(null);
			goto('/', { replaceState: true });
			return;
		}
		if (workspacePath && workspacePath !== lastLoadedPath) {
			// New workspace — load then process intents
			lastLoadedPath = workspacePath;
			loadWorkspace(workspacePath).then(async () => {
				const canonicalWorkspacePath = get(currentWorkspace)?.path;
				if (canonicalWorkspacePath && canonicalWorkspacePath !== workspacePath) {
					const params = new URLSearchParams($page.url.searchParams);
					params.set('workspace', canonicalWorkspacePath);
					lastLoadedPath = canonicalWorkspacePath;
					await goto(`/?${params.toString()}`, { replaceState: true });
				}
				processIntentParams();
			});
		} else if (workspacePath && workspacePath === lastLoadedPath) {
			// Same workspace — process intents immediately
			if (get(currentWorkspace)?.path === workspacePath) processIntentParams();
		} else if (!workspacePath) {
			lastLoadedPath = null;
			currentWorkspace.set(null);
			processIntentParams();
		}
	});

	$effect(() => {
		if (typeof window === 'undefined') return;
		const w = window as LaunchQueueWindow;
		if (!w.launchQueue || w.__cptrLaunchQueueBound) return;
		w.__cptrLaunchQueueBound = true;
		w.launchQueue.setConsumer(async (launchParams) => {
			const handles = launchParams.files ?? [];
			if (!handles.length) return;
			const files: File[] = [];
			for (const handle of handles) {
				try {
					files.push(await handle.getFile());
				} catch {}
			}
			if (files.length) await handleIntent({ kind: 'importFiles', importFiles: { files } });
		});
	});

	$effect(() => {
		if (typeof window === 'undefined') return;
		const handleHomeAction = (event: Event) => {
			if ($currentWorkspace) return;
			switch (
				(
					event as CustomEvent<
						| 'newChat'
						| 'newTerminal'
						| 'newBrowser'
						| 'closeTab'
						| 'nextTab'
						| 'prevTab'
						| 'toggleSplit'
					>
				).detail
			) {
				case 'newChat':
					openHomeChat();
					break;
				case 'newBrowser':
					void openHomeBrowser();
					break;
				case 'closeTab':
					if (
						activeHomeGroup &&
						!activeHomeGroup.tabs.find((tab) => tab.id === activeHomeGroup.activeTabId)?.permanent
					) {
						closeHomeTab(activeHomeGroup.activeTabId, activeHomeGroup.id);
					}
					break;
				case 'nextTab':
					cycleHomeTab(1);
					break;
				case 'prevTab':
					cycleHomeTab(-1);
					break;
				case 'toggleSplit':
					toggleHomeSplit();
					break;
				default:
					void openHomeTerminal();
			}
		};
		window.addEventListener('cptr:home-action', handleHomeAction);
		return () => window.removeEventListener('cptr:home-action', handleHomeAction);
	});

	// Welcome page data
	let welcomeData = $state<{
		hostname: string;
		platform: string;
		version: string;
		system: {
			os: string;
			arch: string;
			python: string;
			cpu_count: number;
			memory_total?: number;
			memory_available?: number;
			disk_total?: number;
			disk_used?: number;
			disk_free?: number;
			uptime_seconds?: number;
			load_avg?: number[];
			cpu_usage?: number;
			network?: { name: string; ip: string }[];
		};
		processes: { pid: number; cpu: number; mem: number; name: string }[];
		suggestions: { name: string; path: string }[];
		recent: { name: string; path: string }[];
	} | null>(null);

	type WorkspaceResume = {
		path: string;
		tabs: Tab[];
		terminalCount: number;
		previewPorts: number[];
		chatCount: number;
		fileCount: number;
		recentChats: ChatInfo[];
		activeChatCount: number;
		activeLabels: string[];
	};

	let workspaceResumes = $state<Map<string, WorkspaceResume>>(new Map());
	let resumeLoadSeq = 0;

	// Fetch welcome data whenever no workspace is active
	$effect(() => {
		if (!$currentWorkspace) {
			getWelcome()
				.then((data) => {
					welcomeData = data as typeof welcomeData;
				})
				.catch(() => {});
		}
	});

	$effect(() => {
		if ($currentWorkspace || !welcomeData) return;
		const paths = [
			...(welcomeData.recent ?? []).map((item) => item.path),
			...(welcomeData.suggestions ?? []).map((item) => item.path)
		]
			.filter((path, index, all) => path && all.indexOf(path) === index)
			.slice(0, 8);
		loadWorkspaceResumes(paths);
	});

	async function loadWorkspaceResumes(paths: string[]) {
		const seq = ++resumeLoadSeq;
		if (!paths.length) {
			workspaceResumes = new Map();
			return;
		}
		const entries = await Promise.all(
			paths.map(async (path) => {
				try {
					const [state, chatsData] = await Promise.all([
						getWorkspaceState(path).catch(() => null),
						getChats(path, 3, 0, 'updated_at', 'desc').catch(() => null)
					]);
					if (!state) return null;
					return [
						path,
						buildWorkspaceResume(path, state as unknown as WorkspaceState, chatsData?.chats ?? [])
					] as const;
				} catch {
					return null;
				}
			})
		);
		if (seq !== resumeLoadSeq) return;
		workspaceResumes = new Map(
			entries.filter((entry): entry is [string, WorkspaceResume] => !!entry)
		);
	}

	function buildWorkspaceResume(
		path: string,
		state: WorkspaceState,
		recentChats: ChatInfo[]
	): WorkspaceResume {
		const tabs = (state.groups ?? []).flatMap((group) => group.tabs ?? []);
		const previewPorts = tabs
			.filter((tab) => tab.type === 'browser' && /^localhost:\d+$/.test(tab.label))
			.map((tab) => Number(tab.label.slice('localhost:'.length)))
			.filter((port, index, all) => all.indexOf(port) === index);
		const activeLabels = tabs
			.filter((tab) => !tab.permanent && ['terminal', 'chat', 'browser', 'file'].includes(tab.type))
			.slice(0, 3)
			.map((tab) => tab.label);

		return {
			path,
			tabs,
			terminalCount: tabs.filter((tab) => tab.type === 'terminal').length,
			previewPorts,
			chatCount: tabs.filter((tab) => tab.type === 'chat').length,
			fileCount: tabs.filter((tab) => tab.type === 'file').length,
			recentChats,
			activeChatCount: recentChats.filter((chat) => chat.is_active).length,
			activeLabels
		};
	}

	// Lazy-init terminal sessions for any group's active tab
	let initingTerminal = $state(false);
	$effect(() => {
		const ws = $currentWorkspace;
		if (!ws || initingTerminal) return;

		// Find any terminal tab across all groups that needs a session
		for (const group of ws.groups) {
			const tab = group.tabs.find((t) => t.id === group.activeTabId);
			if (tab && tab.type === 'terminal' && !tab.sessionId) {
				initingTerminal = true;
				createSession(ws.path)
					.then((data) => {
						currentWorkspace.update((w) => {
							if (!w) return w;
							return {
								...w,
								groups: w.groups.map((g) => ({
									...g,
									tabs: g.tabs.map((t) =>
										t.id === tab.id ? { ...t, sessionId: data.session_id } : t
									)
								}))
							};
						});
					})
					.catch((e) => console.error('Failed to init terminal:', e))
					.finally(() => {
						initingTerminal = false;
					});
				return; // Only init one at a time
			}
		}
	});

	function quickOpen(path: string) {
		addWorkspace(path);
		// Carry forward any pending intent params through workspace selection
		const params = new URLSearchParams($page.url.searchParams);
		params.set('workspace', path);
		goto(`/?${params.toString()}`);
	}

	function shortenPath(path: string): string {
		const home = welcomeData?.suggestions?.[0]?.path;
		if (home && path.startsWith(home)) {
			return '~' + path.slice(home.length);
		}
		return path;
	}

	function resumeSignals(resume: WorkspaceResume | undefined): string[] {
		if (!resume) return [];
		const signals: string[] = [];
		if (resume.terminalCount)
			signals.push($t('home.terminalShort', { count: resume.terminalCount }));
		if (resume.previewPorts.length)
			signals.push(resume.previewPorts.map((port) => `:${port}`).join(', '));
		if (resume.chatCount || resume.activeChatCount) {
			signals.push(
				resume.activeChatCount
					? $t('home.activeChat', { count: resume.activeChatCount })
					: $t('home.chat', { count: resume.chatCount })
			);
		}
		return signals;
	}

	function hasMeaningfulResume(resume: WorkspaceResume | undefined): boolean {
		return !!resume && (resumeSignals(resume).length > 0 || resume.activeLabels.length > 0);
	}

	function continueWorkspace() {
		const recent = welcomeData?.recent ?? [];
		return recent.find((item) => hasMeaningfulResume(workspaceResumes.get(item.path))) ?? recent[0];
	}

	function recentItems(continuePath?: string) {
		const recent = welcomeData?.recent ?? [];
		return recent.filter((item) => item.path !== continuePath).slice(0, 6);
	}

	function nearbyItems() {
		return (welcomeData?.suggestions ?? []).slice(0, 5);
	}

	const continuation = $derived(continueWorkspace());
	const continueResume = $derived(
		continuation ? workspaceResumes.get(continuation.path) : undefined
	);
	const continueSignals = $derived(resumeSignals(continueResume));
	const recent = $derived(recentItems(continuation?.path));
	const nearby = $derived(nearbyItems());

	// ── Draggable divider ──────────────────────────────────────────

	let resizingSplit = $state<{
		splitId: string;
		direction: SplitDirection;
		element: HTMLElement;
	} | null>(null);

	function handleDividerPointerDown(
		e: PointerEvent,
		split: Extract<EditorLayout, { type: 'split' }>
	) {
		e.preventDefault();
		const divider = e.currentTarget as HTMLElement;
		resizingSplit = {
			splitId: split.id,
			direction: split.direction,
			element: divider.parentElement!
		};
		divider.setPointerCapture(e.pointerId);
	}

	function handleDividerPointerMove(e: PointerEvent) {
		if (!resizingSplit) return;
		const rect = resizingSplit.element.getBoundingClientRect();

		let ratio: number;
		if (resizingSplit.direction === 'horizontal') {
			ratio = (e.clientX - rect.left) / rect.width;
		} else {
			ratio = (e.clientY - rect.top) / rect.height;
		}
		if ($currentWorkspace) setSplitRatio(resizingSplit.splitId, ratio);
		else setHomeSplitRatio(resizingSplit.splitId, ratio);
	}

	function handleDividerPointerUp() {
		resizingSplit = null;
	}

	// Computed
	let isWideScreen = $state(typeof window !== 'undefined' ? window.innerWidth >= 1024 : false);

	$effect(() => {
		if (typeof window === 'undefined') return;
		function onResize() {
			isWideScreen = window.innerWidth >= 1024;
		}
		window.addEventListener('resize', onResize);
		return () => window.removeEventListener('resize', onResize);
	});

	const allGroups = $derived($currentWorkspace?.groups ?? []);
	const layout = $derived($currentWorkspace?.layout ?? null);
	const activeGroup = $derived(
		allGroups.find((group) => group.id === $currentWorkspace?.activeGroupId) ?? allGroups[0] ?? null
	);

	function getGroupActiveTab(group: EditorGroup): Tab | null {
		return group.tabs.find((t) => t.id === group.activeTabId) ?? null;
	}

	// ── Drag-to-split ─────────────────────────────────────────────

	const SPLIT_EDGE_FRACTION = 0.3;

	type SplitDropZone = 'left' | 'right' | 'top' | 'bottom';
	type TabDragPayload = { tabId: string; groupId: string };

	let dragOverZone = $state<{ groupId: string; zone: SplitDropZone } | null>(null);

	$effect(() => {
		const clearSplitPreview = () => (dragOverZone = null);
		document.addEventListener('dragend', clearSplitPreview, true);
		document.addEventListener('drop', clearSplitPreview, true);
		window.addEventListener('blur', clearSplitPreview);
		return () => {
			document.removeEventListener('dragend', clearSplitPreview, true);
			document.removeEventListener('drop', clearSplitPreview, true);
			window.removeEventListener('blur', clearSplitPreview);
		};
	});

	function hasTabDrag(dataTransfer: DataTransfer): boolean {
		return dataTransfer.types.includes(TAB_DRAG_MIME) || dataTransfer.types.includes('text/tab-id');
	}

	function readTabDragPayload(dataTransfer: DataTransfer): TabDragPayload | null {
		const raw = dataTransfer.getData(TAB_DRAG_MIME);
		if (raw) {
			try {
				const parsed = JSON.parse(raw) as Partial<TabDragPayload>;
				if (typeof parsed.tabId === 'string' && typeof parsed.groupId === 'string') {
					return { tabId: parsed.tabId, groupId: parsed.groupId };
				}
			} catch {
				// Fall through to the legacy payload below.
			}
		}

		const tabId = dataTransfer.getData('text/tab-id');
		const groupId = dataTransfer.getData('text/group-id');
		return tabId && groupId ? { tabId, groupId } : null;
	}

	function getSplitDropZone(e: DragEvent, pane: HTMLElement): SplitDropZone | null {
		if (!isWideScreen || !e.dataTransfer) return null;
		if (e.dataTransfer.types.includes('Files') || !hasTabDrag(e.dataTransfer)) return null;

		const rect = pane.getBoundingClientRect();
		if (
			e.clientX < rect.left ||
			e.clientX > rect.right ||
			e.clientY < rect.top ||
			e.clientY > rect.bottom
		) {
			return null;
		}

		const horizontalBand = rect.width * SPLIT_EDGE_FRACTION;
		const verticalBand = rect.height * SPLIT_EDGE_FRACTION;
		const leftDistance = e.clientX - rect.left;
		const rightDistance = rect.right - e.clientX;
		const topDistance = e.clientY - rect.top;
		const bottomDistance = rect.bottom - e.clientY;
		const candidates: { zone: SplitDropZone; distance: number }[] = [];

		if (leftDistance <= horizontalBand) candidates.push({ zone: 'left', distance: leftDistance });
		if (rightDistance <= horizontalBand)
			candidates.push({ zone: 'right', distance: rightDistance });
		if (topDistance <= verticalBand) candidates.push({ zone: 'top', distance: topDistance });
		if (bottomDistance <= verticalBand)
			candidates.push({ zone: 'bottom', distance: bottomDistance });

		candidates.sort((a, b) => a.distance - b.distance);
		return candidates[0]?.zone ?? null;
	}

	function handlePaneDragOver(e: DragEvent, groupId: string) {
		const zone = getSplitDropZone(e, e.currentTarget as HTMLElement);
		if (!zone) {
			if (dragOverZone?.groupId === groupId) dragOverZone = null;
			return;
		}

		e.preventDefault();
		e.dataTransfer!.dropEffect = 'move';
		dragOverZone = { groupId, zone };
	}

	function handlePaneDragLeave(e: DragEvent, groupId: string) {
		if (
			!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node) &&
			dragOverZone?.groupId === groupId
		) {
			dragOverZone = null;
		}
	}

	function handlePaneDrop(e: DragEvent, targetGroupId: string) {
		const zone =
			dragOverZone?.groupId === targetGroupId
				? dragOverZone.zone
				: getSplitDropZone(e, e.currentTarget as HTMLElement);

		if (!zone || !e.dataTransfer) {
			dragOverZone = null;
			return;
		}

		const payload = readTabDragPayload(e.dataTransfer);
		if (!payload) {
			dragOverZone = null;
			return;
		}

		e.preventDefault();
		const direction: SplitDirection =
			zone === 'left' || zone === 'right' ? 'horizontal' : 'vertical';
		const placement = zone === 'left' || zone === 'top' ? 'before' : 'after';
		if ($currentWorkspace) {
			moveTabToNewSplit(payload.tabId, payload.groupId, targetGroupId, direction, placement);
		} else {
			moveHomeTabToNewSplit(payload.tabId, payload.groupId, targetGroupId, direction, placement);
		}
		dragOverZone = null;
	}
</script>

{#if !$currentWorkspace}
	{#snippet renderHomePane(homePane: EditorGroup)}
		{@const homeTab =
			homePane.tabs.find((tab) => tab.id === homePane.activeTabId) ?? homePane.tabs[0]}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="split-pane"
			onpointerdown={() => setHomeActiveGroup(homePane.id)}
			onfocusin={() => setHomeActiveGroup(homePane.id)}
			ondragover={(event) => handlePaneDragOver(event, homePane.id)}
			ondragleave={(event) => handlePaneDragLeave(event, homePane.id)}
			ondrop={(event) => handlePaneDrop(event, homePane.id)}
		>
			<GroupTabBar
				group={homePane}
				home
				isPrimary={homePane.id === $homeState.groups[0]?.id}
				canClose={$homeState.groups.length > 1}
				homeActive={homePane.id === $homeState.activeGroupId}
				homeSplitActive={$homeState.groups.length > 1}
				homeSplitDirection={$homeState.splitDirection}
				onHomeSelect={(tabId) =>
					updateHomeTabs(homePane.id, (tabs) => ({ tabs, activeTabId: tabId }))}
				onHomeClose={(tabId) => closeHomeTab(tabId, homePane.id)}
				onHomeReorder={(oldIndex, newIndex) => reorderHomeTabs(homePane.id, oldIndex, newIndex)}
				onHomeMove={(tabId, fromGroupId) => moveHomeTabToGroup(tabId, fromGroupId, homePane.id)}
				onHomeNewChat={() => openHomeChat(undefined, homePane.id)}
				onHomeNewTerminal={() => openHomeTerminal(homePane.id)}
				onHomeNewBrowser={() => openHomeBrowser(undefined, homePane.id)}
				onHomeSplit={(direction) => {
					setHomeActiveGroup(homePane.id);
					splitHomeTab(direction);
				}}
				onHomeCloseGroup={() => closeHomeGroup(homePane.id)}
				onTabDragOver={() => {
					if (dragOverZone?.groupId === homePane.id) dragOverZone = null;
				}}
			/>
			<div class="pane-content">
				{#each homePane.tabs.filter((tab) => tab.type === 'chat') as tab (tab.id)}
					<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== homePane.activeTabId}>
						<ChatPanel
							chatId={tab.path?.startsWith('new-') || tab.path?.startsWith('pending-')
								? undefined
								: tab.path}
							tabId={tab.id}
							active={tab.id === homePane.activeTabId}
							ontabupdate={(tabId, chatId, label) =>
								updateHomeChatTab(tabId, chatId, label, homePane.id)}
							onopenchat={(chatId) => openHomeChat(chatId, homePane.id)}
						/>
					</div>
				{/each}
				{#each homePane.tabs.filter((tab) => tab.type === 'file' && tab.filePath) as tab (tab.id)}
					<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== homePane.activeTabId}>
						<FileEditor
							filePath={tab.filePath!}
							tabId={tab.id}
							edit={tab.edit === true}
							searchTarget={tab.searchTarget}
						/>
					</div>
				{/each}
				{#each homePane.tabs.filter((tab) => tab.type === 'terminal' && tab.sessionId) as tab (tab.id)}
					<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== homePane.activeTabId}>
						<Terminal sessionId={tab.sessionId!} />
					</div>
				{/each}
				{#each homePane.tabs.filter((tab) => tab.type === 'browser' && tab.browserSessionId) as tab (tab.id)}
					<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== homePane.activeTabId}>
						<BrowserPreview
							sessionId={tab.browserSessionId!}
							groupId={homePane.id}
							tabId={tab.id}
							initialUrl={tab.path}
							active={tab.id === homePane.activeTabId && homePane.id === $homeState.activeGroupId}
							onTabUpdate={(label) => updateHomeBrowserTab(tab.id, label, homePane.id)}
							onOpenBrowser={(url) => openHomeBrowser(url, homePane.id)}
						/>
					</div>
				{/each}
				{#if homeTab?.type === 'home'}
					<div class="h-full overflow-y-auto px-6">
						<div class="mx-auto flex min-h-full w-full max-w-md flex-col justify-center py-5">
							<div class="mb-4">
								<div class="flex items-baseline gap-2">
									<h1 class="text-lg font-medium tracking-tight text-gray-900 dark:text-white">
										{#if welcomeName}
											{@const greeting = $t(`home.greeting.${greetingTime}.${greetingVariant}`, {
												name: greetingNameMarker
											})}
											{@const [beforeName, afterName] = greeting.split(greetingNameMarker)}
											{beforeName}<span class="capitalize">{welcomeName}</span>{afterName}
										{:else}
											Computer
										{/if}
									</h1>
								</div>
								<div
									class="mt-0.5 flex items-baseline gap-2 font-mono text-xs text-gray-400 dark:text-gray-600"
								>
									{#if welcomeData?.hostname}
										<span class="text-[0.6875rem]">{welcomeData.hostname}</span>
									{/if}
									{#if $appVersion}
										<button
											onclick={() => showChangelog.set(true)}
											class="cursor-pointer text-[0.6875rem] hover:text-gray-500 hover:underline dark:hover:text-gray-400"
										>
											v{$appVersion}
										</button>
									{/if}
								</div>
							</div>

							<div class="mb-5">
								<h2 class="mb-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
									{$t('home.start')}
								</h2>
								<button
									class="text-xs text-gray-600 transition-colors duration-100 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
									onclick={() => (showPicker = true)}
								>
									{$t('home.openWorkspace')}
								</button>
								{#if $chatEnabled}
									<button
										class="mt-1 block text-xs text-gray-600 transition-colors duration-100 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
										onclick={() => openHomeChat(undefined, homePane.id)}
									>
										{$t('bar.newChat')}
									</button>
								{/if}
							</div>

							{#if continuation}
								{@const unreadCount =
									$workspaceList.find((workspace) => workspace.path === continuation.path)
										?.unread_count ?? 0}
								<div class="mb-4">
									<h2 class="mb-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
										{$t('home.continue')}
									</h2>
									<button
										class="group w-full min-w-0 py-1 text-left transition-colors duration-100"
										onclick={() => quickOpen(continuation.path)}
									>
										<span class="flex min-w-0 items-baseline gap-2">
											<span class="flex min-w-0 items-center gap-1.5">
												<span
													class="truncate text-xs text-gray-800 group-hover:text-gray-950 dark:text-gray-200 dark:group-hover:text-white"
												>
													{continuation.name}
												</span>
												{#if unreadCount > 0}
													<span
														class="inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-md bg-sky-500/10 px-1 text-[0.625rem] font-semibold text-sky-600 dark:bg-sky-400/10 dark:text-sky-300"
													>
														{new Intl.NumberFormat(undefined, {
															notation: 'compact',
															compactDisplay: 'short'
														}).format(unreadCount)}
													</span>
												{/if}
											</span>
											<span
												class="truncate font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600"
											>
												{shortenPath(continuation.path)}
											</span>
										</span>
										{#if continueSignals.length}
											<span
												class="mt-0.5 block truncate font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
											>
												{continueSignals.join('  ')}
											</span>
										{:else if continueResume?.activeLabels.length}
											<span
												class="mt-0.5 block truncate text-[0.6875rem] text-gray-400 dark:text-gray-600"
											>
												{continueResume.activeLabels.join(' · ')}
											</span>
										{/if}
									</button>
								</div>
							{/if}

							{#if recent.length}
								<div class="mb-4">
									<h2 class="mb-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
										{$t('home.recent')}
									</h2>
									<div class="flex flex-col">
										{#each recent as item}
											{@const resume = workspaceResumes.get(item.path)}
											{@const signals = resumeSignals(resume)}
											{@const unreadCount =
												$workspaceList.find((workspace) => workspace.path === item.path)
													?.unread_count ?? 0}
											<button
												class="group w-full min-w-0 py-1 text-left transition-colors duration-100"
												onclick={() => quickOpen(item.path)}
											>
												<span class="flex min-w-0 items-baseline gap-2">
													<span class="flex min-w-0 items-center gap-1.5">
														<span
															class="truncate text-xs text-gray-700 group-hover:text-gray-900 dark:text-gray-300 dark:group-hover:text-white"
														>
															{item.name}
														</span>
														{#if unreadCount > 0}
															<span
																class="inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-md bg-sky-500/10 px-1 text-[0.625rem] font-semibold text-sky-600 dark:bg-sky-400/10 dark:text-sky-300"
															>
																{new Intl.NumberFormat(undefined, {
																	notation: 'compact',
																	compactDisplay: 'short'
																}).format(unreadCount)}
															</span>
														{/if}
													</span>
													<span
														class="truncate font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600"
													>
														{shortenPath(item.path)}
													</span>
												</span>
												{#if signals.length}
													<span
														class="mt-0.5 block truncate font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
													>
														{signals.join('  ')}
													</span>
												{:else if resume?.activeLabels.length}
													<span
														class="mt-0.5 block truncate text-[0.6875rem] text-gray-400 dark:text-gray-600"
													>
														{resume.activeLabels.join(' · ')}
													</span>
												{/if}
											</button>
										{/each}
									</div>
								</div>
							{:else if !continuation}
								<div class="mb-4">
									<h2 class="mb-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
										{$t('home.recent')}
									</h2>
									<button
										class="text-xs text-gray-500 transition-colors duration-100 hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
										onclick={() => (showPicker = true)}
									>
										{$t('home.noWorkspaces')}
									</button>
								</div>
							{/if}

							{#if nearby.length && !welcomeData?.recent?.length}
								<div>
									<h2 class="mb-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
										{$t('home.folders')}
									</h2>
									<div class="flex flex-col">
										{#each nearby as item}
											{@const unreadCount =
												$workspaceList.find((workspace) => workspace.path === item.path)
													?.unread_count ?? 0}
											<button
												class="flex min-w-0 items-center gap-2 py-1 text-left text-xs text-gray-600 transition-colors duration-100 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
												onclick={() => quickOpen(item.path)}
											>
												<Icon
													name="folder"
													size={14}
													strokeWidth={1.3}
													class="shrink-0 text-gray-400 dark:text-gray-600"
												/>
												<span class="flex min-w-0 items-center gap-1.5">
													<span class="truncate">{item.name}</span>
													{#if unreadCount > 0}
														<span
															class="inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-md bg-sky-500/10 px-1 text-[0.625rem] font-semibold text-sky-600 dark:bg-sky-400/10 dark:text-sky-300"
														>
															{new Intl.NumberFormat(undefined, {
																notation: 'compact',
																compactDisplay: 'short'
															}).format(unreadCount)}
														</span>
													{/if}
												</span>
												<span
													class="truncate font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600"
												>
													{shortenPath(item.path)}
												</span>
											</button>
										{/each}
									</div>
								</div>
							{/if}
						</div>
					</div>
				{/if}
			</div>
			{#if dragOverZone?.groupId === homePane.id}
				<div class={`split-drop-zone split-drop-${dragOverZone.zone}`}></div>
			{/if}
		</div>
	{/snippet}

	{#snippet renderHomeLayout(node: EditorLayout)}
		{#if node.type === 'group'}
			{@const group = $homeState.groups.find((item) => item.id === node.groupId)}
			{#if group}{@render renderHomePane(group)}{/if}
		{:else}
			<div
				class="split-branch"
				class:split-branch-horizontal={node.direction === 'horizontal'}
				class:split-branch-vertical={node.direction === 'vertical'}
			>
				<div class="split-branch-child" style={`flex: ${node.ratio} 1 0%;`}>
					{@render renderHomeLayout(node.first)}
				</div>
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div
					class="split-divider"
					class:split-divider-h={node.direction === 'horizontal'}
					class:split-divider-v={node.direction === 'vertical'}
					onpointerdown={(event) => handleDividerPointerDown(event, node)}
					onpointermove={handleDividerPointerMove}
					onpointerup={handleDividerPointerUp}
					onpointercancel={handleDividerPointerUp}
				></div>
				<div class="split-branch-child" style={`flex: ${1 - node.ratio} 1 0%;`}>
					{@render renderHomeLayout(node.second)}
				</div>
			</div>
		{/if}
	{/snippet}

	<div class="split-container" class:is-dragging={resizingSplit !== null} role="presentation">
		{#if isWideScreen}
			{@render renderHomeLayout($homeState.layout)}
		{:else if activeHomeGroup}
			{@render renderHomePane(activeHomeGroup)}
		{/if}
	</div>
{:else}
	<!-- Editor groups layout -->
	<div class="split-container" class:is-dragging={resizingSplit !== null} role="presentation">
		{#if isWideScreen && layout}
			{@render renderLayout(layout)}
		{:else if activeGroup}
			{@render renderPane(activeGroup)}
		{/if}
	</div>
{/if}

{#snippet renderLayout(node: EditorLayout)}
	{#if node.type === 'group'}
		{@const group = allGroups.find((item) => item.id === node.groupId)}
		{#if group}{@render renderPane(group)}{/if}
	{:else}
		<div
			class="split-branch"
			class:split-branch-horizontal={node.direction === 'horizontal'}
			class:split-branch-vertical={node.direction === 'vertical'}
		>
			<div class="split-branch-child" style={`flex: ${node.ratio} 1 0%;`}>
				{@render renderLayout(node.first)}
			</div>
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="split-divider"
				class:split-divider-h={node.direction === 'horizontal'}
				class:split-divider-v={node.direction === 'vertical'}
				onpointerdown={(event) => handleDividerPointerDown(event, node)}
				onpointermove={handleDividerPointerMove}
				onpointerup={handleDividerPointerUp}
				onpointercancel={handleDividerPointerUp}
			></div>
			<div class="split-branch-child" style={`flex: ${1 - node.ratio} 1 0%;`}>
				{@render renderLayout(node.second)}
			</div>
		</div>
	{/if}
{/snippet}

{#snippet renderPane(group: EditorGroup)}
	{@const groupTab = getGroupActiveTab(group)}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="split-pane"
		onpointerdown={() => setActiveGroup(group.id)}
		onfocusin={() => setActiveGroup(group.id)}
		ondragover={(event) => handlePaneDragOver(event, group.id)}
		ondragleave={(event) => handlePaneDragLeave(event, group.id)}
		ondrop={(event) => handlePaneDrop(event, group.id)}
	>
		<GroupTabBar
			{group}
			canClose={allGroups.length > 1}
			isPrimary={group.id === allGroups[0]?.id}
			onTabDragOver={() => {
				if (dragOverZone?.groupId === group.id) dragOverZone = null;
			}}
		/>
		<div class="pane-content">
			{#each group.tabs.filter((tab) => tab.type === 'files') as tab (tab.id)}
				<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== group.activeTabId}>
					<FileBrowser />
				</div>
			{/each}
			{#each group.tabs.filter((tab) => tab.type === 'file' && tab.filePath) as tab (tab.id)}
				<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== group.activeTabId}>
					<FileEditor
						filePath={tab.filePath!}
						tabId={tab.id}
						edit={tab.edit === true}
						searchTarget={tab.searchTarget}
					/>
				</div>
			{/each}
			{#each group.tabs.filter((tab) => tab.type === 'chat') as tab (tab.id)}
				<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== group.activeTabId}>
					<ChatPanel
						workspace={$currentWorkspace!.path}
						chatId={tab.path?.startsWith('new-') || tab.path?.startsWith('pending-')
							? undefined
							: tab.path}
						tabId={tab.id}
						active={tab.id === group.activeTabId}
						onopenchat={(chatId) => openChatTab(chatId, group.id)}
					/>
				</div>
			{/each}
			{#each group.tabs.filter((tab) => tab.type === 'terminal' && tab.sessionId) as tab (tab.id)}
				<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== group.activeTabId}>
					<Terminal sessionId={tab.sessionId!} />
				</div>
			{/each}
			{#each group.tabs.filter((tab) => tab.type === 'browser' && tab.browserSessionId) as tab (tab.id)}
				<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== group.activeTabId}>
					<BrowserPreview
						sessionId={tab.browserSessionId!}
						groupId={group.id}
						tabId={tab.id}
						initialUrl={tab.path}
						active={tab.id === group.activeTabId && group.id === activeGroup?.id}
					/>
				</div>
			{/each}
			{#if !groupTab}
				<FileBrowser />
			{:else if groupTab.type === 'terminal' && !groupTab.sessionId}
				<div class="flex items-center justify-center h-full"><Spinner size={20} /></div>
			{:else if groupTab.type === 'browser' && !groupTab.browserSessionId}
				<div
					class="flex h-full items-center justify-center gap-2 text-xs text-gray-500 dark:text-gray-400"
				>
					<Spinner size={16} />
					<span>{$t('browser.starting')}</span>
				</div>
			{/if}
			{#if $gitReviewOpen && group.id === allGroups[0]?.id}
				<div class="persisted-tab git-review-panel">
					<GitView />
				</div>
			{/if}
		</div>
		{#if dragOverZone?.groupId === group.id}
			<div class={`split-drop-zone split-drop-${dragOverZone.zone}`}></div>
		{/if}
	</div>
{/snippet}

{#if pendingIntent}
	<WorkspacePicker
		intent={pendingIntent}
		workspaces={$workspaceList}
		onchoose={chooseIntentWorkspace}
		oncancel={() => (pendingIntent = null)}
	/>
{/if}

{#if showPicker}
	<DirectoryPicker
		onclose={() => {
			showPicker = false;
			folderPickerIntent = null;
			folderPickerWorkspace = null;
		}}
		onselect={folderPickerIntent ? handlePickedImportFolder : undefined}
	/>
{/if}

<style>
	@reference "../app.css";

	/* Container */
	.split-container {
		display: flex;
		width: 100%;
		height: 100%;
		position: relative;
		overflow: hidden;
		background: var(--app-bg);
		color: var(--app-fg);
	}

	.split-branch {
		display: flex;
		flex: 1;
		min-width: 0;
		min-height: 0;
		overflow: hidden;
	}

	.split-branch-horizontal {
		flex-direction: row;
	}

	.split-branch-vertical {
		flex-direction: column;
	}

	.split-branch-child {
		display: flex;
		min-width: 0;
		min-height: 0;
		overflow: hidden;
	}

	/* Panes */
	.split-pane {
		display: flex;
		flex: 1;
		flex-direction: column;
		position: relative;
		overflow: hidden;
		min-width: 0;
		min-height: 0;
	}

	.pane-content {
		flex: 1;
		min-height: 0;
		min-width: 0;
		overflow: hidden;
		position: relative;
		background: var(--app-bg);
		color: var(--app-fg);
	}

	/* ── Divider ─────────────────────────────────────────────── */
	.split-divider {
		flex-shrink: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 10;
		position: relative;
		transition: background 0.15s;
	}

	.split-divider-h {
		width: 0.375rem;
		cursor: col-resize;
	}

	.split-divider-v {
		height: 0.375rem;
		cursor: row-resize;
	}

	.split-divider-handle {
		display: none;
	}

	.split-divider::before {
		content: '';
		position: absolute;
		z-index: -1;
	}

	.split-divider-h::before {
		width: 1px;
		height: 100%;
		left: 50%;
		transform: translateX(-50%);
		background: var(--app-divider);
	}

	:global(.dark) .split-divider-h::before {
		background: var(--app-divider);
	}

	.split-divider-v::before {
		height: 1px;
		width: 100%;
		top: 50%;
		transform: translateY(-50%);
		background: var(--app-divider);
	}

	:global(.dark) .split-divider-v::before {
		background: var(--app-divider);
	}

	.split-divider:hover,
	.is-dragging .split-divider {
		background: var(--app-active);
	}

	/* Drag state */
	.is-dragging {
		user-select: none;
	}

	/* ── Drop zones for drag-to-split ─────────────────── */
	.split-drop-zone {
		position: absolute;
		z-index: 15;
		display: block;
		flex: none;
		contain: layout paint;
		pointer-events: none;
		--split-tabbar-height: 2.25rem;
		background: color-mix(in oklab, var(--app-fg) 6%, transparent);
		box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--app-fg) 16%, transparent);
	}

	.split-drop-left,
	.split-drop-right {
		top: var(--split-tabbar-height);
		bottom: 0;
		width: 50%;
	}

	.split-drop-left {
		left: 0;
	}

	.split-drop-right {
		right: 0;
	}

	.split-drop-top,
	.split-drop-bottom {
		left: 0;
		right: 0;
		height: calc((100% - var(--split-tabbar-height)) / 2);
	}

	.split-drop-top {
		top: var(--split-tabbar-height);
	}

	.split-drop-bottom {
		bottom: 0;
	}

	/* ── Persisted file editor tabs ────────────────────── */
	.persisted-tab {
		position: absolute;
		inset: 0;
		z-index: 1;
		overflow: hidden;
	}

	.persisted-tab-hidden {
		visibility: hidden;
		z-index: 0;
		pointer-events: none;
	}

	.git-review-panel {
		z-index: 2;
	}
</style>
