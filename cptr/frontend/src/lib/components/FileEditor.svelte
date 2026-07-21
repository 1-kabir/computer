<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import {
		clearFileSearchTarget,
		clearTabEdit,
		markTabUnsaved,
		updateTabFilePath,
		activeWorkspace,
		type FileSearchTarget
	} from '$lib/stores';
	import { get } from 'svelte/store';
	import { t } from '$lib/i18n';
	import { tooltip } from '$lib/tooltip';
	import { readFile, writeFile } from '$lib/apis/files';
	import { getGitDiff } from '$lib/apis/git';
	import { diffDisplayMode, hideWhitespaceChanges } from '$lib/stores/gitDiffSettings';
	import { gitStatusStore, type GitFile } from '$lib/stores/gitStatus.svelte';
	import Icon from './Icon.svelte';
	import SaveDialog from './SaveDialog.svelte';
	import type RichTextEditorType from './markdown/RichTextEditor.svelte';
	import MarkdownRenderer from './markdown/MarkdownRenderer.svelte';
	import ImagePreview from './preview/ImagePreview.svelte';
	import PDFViewer from './preview/PDFViewer.svelte';
	import JsonTreeView from './preview/JsonTreeView.svelte';
	import CsvTable from './preview/CsvTable.svelte';
	import SqliteView from './preview/SqliteView.svelte';
	import HtmlPreview from './preview/HtmlPreview.svelte';
	import SvgPreview from './preview/SvgPreview.svelte';
	import OfficePreview from './preview/OfficePreview.svelte';
	import Spinner from './common/Spinner.svelte';
	import DiffHunkList from './DiffHunkList.svelte';
	import {
		EditorState,
		StateEffect,
		StateField,
		RangeSetBuilder,
		type RangeSet
	} from '@codemirror/state';
	import {
		EditorView,
		GutterMarker,
		keymap,
		lineNumbers,
		gutterLineClass,
		highlightActiveLineGutter,
		highlightSpecialChars,
		drawSelection,
		highlightActiveLine,
		rectangularSelection,
		type ViewUpdate
	} from '@codemirror/view';
	import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
	import {
		syntaxHighlighting,
		defaultHighlightStyle,
		indentOnInput,
		bracketMatching,
		foldGutter,
		foldKeymap,
		StreamLanguage
	} from '@codemirror/language';
	import {
		autocompletion,
		closeBrackets,
		closeBracketsKeymap,
		completeAnyWord,
		completionKeymap
	} from '@codemirror/autocomplete';
	import { searchKeymap, highlightSelectionMatches } from '@codemirror/search';
	import { oneDark } from '@codemirror/theme-one-dark';

	import { javascript } from '@codemirror/lang-javascript';
	import { python } from '@codemirror/lang-python';
	import { json } from '@codemirror/lang-json';
	import { css } from '@codemirror/lang-css';
	import { html } from '@codemirror/lang-html';
	import { markdown } from '@codemirror/lang-markdown';
	import { rust } from '@codemirror/lang-rust';
	import { cpp } from '@codemirror/lang-cpp';
	import { java } from '@codemirror/lang-java';
	import { sql } from '@codemirror/lang-sql';
	import { xml } from '@codemirror/lang-xml';
	import { yaml } from '@codemirror/lang-yaml';
	import { shell } from '@codemirror/legacy-modes/mode/shell';

	interface Props {
		filePath: string;
		tabId: string;
		edit?: boolean;
		searchTarget?: FileSearchTarget;
	}

	let { filePath, tabId, edit = false, searchTarget }: Props = $props();

	interface FileData {
		path: string;
		name: string;
		size: number;
		binary: boolean;
		content: string | null;
		language: string | null;
	}

	// ── File type detection ──────────────────────────────────────
	const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'ico', 'avif', 'tiff']);
	const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'ogv', 'avi', 'mkv']);
	const AUDIO_EXTS = new Set(['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'opus', 'wma']);
	const PDF_EXTS = new Set(['pdf']);
	const CSV_EXTS = new Set(['csv', 'tsv']);
	const SQLITE_EXTS = new Set(['sqlite', 'sqlite3', 'db', 'db3']);
	const OFFICE_EXTS = new Set(['docx', 'xlsx', 'xls', 'pptx']);

	function getExt(path: string): string {
		return path.split('.').pop()?.toLowerCase() ?? '';
	}

	let ext = $derived(getExt(filePath));
	let isImage = $derived(IMAGE_EXTS.has(ext));
	let isVideo = $derived(VIDEO_EXTS.has(ext));
	let isAudio = $derived(AUDIO_EXTS.has(ext));
	let isPdf = $derived(PDF_EXTS.has(ext));
	let isCsv = $derived(CSV_EXTS.has(ext));
	let isTsv = $derived(ext === 'tsv');
	let isJson = $derived(ext === 'json' || ext === 'jsonc' || ext === 'json5');
	let isMarkdown = $derived(ext === 'md' || ext === 'markdown' || ext === 'mdx');
	let isSvg = $derived(ext === 'svg');
	let isHtml = $derived(ext === 'html' || ext === 'htm');
	let isSqlite = $derived(SQLITE_EXTS.has(ext));
	let isOffice = $derived(OFFICE_EXTS.has(ext));
	let isBinaryPreview = $derived(isImage || isVideo || isAudio || isPdf || isSqlite || isOffice);

	// ── State ────────────────────────────────────────────────────
	let editorEl: HTMLDivElement | undefined = $state();
	let view: EditorView | null = null;
	let fileData = $state<FileData | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let saving = $state(false);
	let saved = $state(false);
	let hasChanges = $state(false);
	let showSaveDialog = $state(false);

	// Detect untitled files
	let isUntitled = $derived(filePath.startsWith('untitled:'));

	// Binary preview state
	let binaryUrl = $state<string | null>(null);

	// Markdown mode: preview (read-only), editor (WYSIWYG), raw (CodeMirror source)
	type MarkdownMode = 'preview' | 'editor' | 'raw';
	let markdownMode = $state<MarkdownMode>('preview');
	let openedInEdit = $state(false);

	// Rich text editor, lazy loaded
	let RichTextEditor: typeof RichTextEditorType | null = $state(null);
	let richTextRef: { getMarkdown: () => string; getEditor: () => any } | undefined = $state();

	// HTML/SVG preview mode
	let previewMode = $state(true);

	// JSON tree view
	let jsonData = $state<unknown>(null);
	let jsonParseError = $state(false);
	let jsonViewMode = $state<'tree' | 'source'>('tree');

	// Diff mode
	type DiffLine = { type: 'added' | 'removed' | 'context'; content: string };
	type DiffHunk = { header: string; lines: DiffLine[] };
	type DiffFileEntry = { path: string; hunks: DiffHunk[] };
	type NumberedLine = DiffLine & { oldNumber: number | null; newNumber: number | null };
	type GitLineChange = { line: number; type: 'added' | 'modified' };
	let diffMode = $state(false);
	let diffFiles = $state<DiffFileEntry[]>([]);
	let diffLoading = $state(false);
	let diffScrollEl: HTMLDivElement | undefined;
	let hasGitChanges = $state(false);
	let gitLineChanges: GitLineChange[] = [];
	let gitBaseContent: string | null = null;
	let gitLineRefreshSeq = 0;
	let lastGitLineRefreshKey = '';

	const setGitLineChangesEffect = StateEffect.define<GitLineChange[]>();

	class GitChangeMarker extends GutterMarker {
		readonly type: GitLineChange['type'];
		readonly elementClass: string;

		constructor(type: GitLineChange['type']) {
			super();
			this.type = type;
			this.elementClass = `cm-git-line-${type}`;
		}

		eq(other: GutterMarker) {
			return other instanceof GitChangeMarker && other.type === this.type;
		}
	}

	const gitAddedMarker = new GitChangeMarker('added');
	const gitModifiedMarker = new GitChangeMarker('modified');

	function gitMarkerFor(change: GitLineChange): GitChangeMarker {
		return change.type === 'added' ? gitAddedMarker : gitModifiedMarker;
	}

	function normalizeGitLineChanges(changes: GitLineChange[]): GitLineChange[] {
		const byLine = new Map<number, GitLineChange['type']>();
		for (const change of changes) {
			if (change.line < 1) continue;
			const current = byLine.get(change.line);
			byLine.set(
				change.line,
				current === 'added' || change.type === 'added' ? 'added' : 'modified'
			);
		}
		return [...byLine.entries()]
			.sort((a, b) => a[0] - b[0])
			.map(([line, type]) => ({ line, type }));
	}

	function buildGitGutterMarkers(
		doc: EditorState['doc'],
		changes: GitLineChange[]
	): RangeSet<GutterMarker> {
		const builder = new RangeSetBuilder<GutterMarker>();
		for (const change of normalizeGitLineChanges(changes)) {
			if (change.line < 1 || change.line > doc.lines) continue;
			const line = doc.line(change.line);
			builder.add(line.from, line.from, gitMarkerFor(change));
		}
		return builder.finish();
	}

	const gitLineChangeField = StateField.define<{
		changes: GitLineChange[];
		markers: RangeSet<GutterMarker>;
	}>({
		create(state) {
			return {
				changes: [],
				markers: buildGitGutterMarkers(state.doc, [])
			};
		},
		update(value, transaction) {
			for (const effect of transaction.effects) {
				if (effect.is(setGitLineChangesEffect)) {
					return {
						changes: effect.value,
						markers: buildGitGutterMarkers(transaction.state.doc, effect.value)
					};
				}
			}
			if (transaction.docChanged) {
				return {
					...value,
					markers: buildGitGutterMarkers(transaction.state.doc, value.changes)
				};
			}
			return value;
		},
		provide: (field) => [gutterLineClass.from(field, (value) => value.markers)]
	});

	function diffHunkStart(header: string): { oldStart: number; newStart: number } {
		const match = header.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
		return { oldStart: match ? Number(match[1]) : 0, newStart: match ? Number(match[2]) : 0 };
	}

	function diffNumberedLines(hunk: DiffHunk): NumberedLine[] {
		let { oldStart, newStart } = diffHunkStart(hunk.header);
		return hunk.lines.map((line) => {
			if (line.type === 'added') return { ...line, oldNumber: null, newNumber: newStart++ };
			if (line.type === 'removed') return { ...line, oldNumber: oldStart++, newNumber: null };
			return { ...line, oldNumber: oldStart++, newNumber: newStart++ };
		});
	}

	function splitLines(text: string): string[] {
		return text.length === 0 ? [] : text.split('\n');
	}

	function lineChangesBetween(baseContent: string, currentContent: string): GitLineChange[] {
		const oldLines = splitLines(baseContent);
		const newLines = splitLines(currentContent);
		if (oldLines.length === 0) {
			return newLines.map((_, i) => ({ line: i + 1, type: 'added' }));
		}
		if (newLines.length === 0) return [];

		let start = 0;
		while (
			start < oldLines.length &&
			start < newLines.length &&
			oldLines[start] === newLines[start]
		) {
			start++;
		}

		let oldEnd = oldLines.length - 1;
		let newEnd = newLines.length - 1;
		while (oldEnd >= start && newEnd >= start && oldLines[oldEnd] === newLines[newEnd]) {
			oldEnd--;
			newEnd--;
		}

		const oldMid = oldLines.slice(start, oldEnd + 1);
		const newMid = newLines.slice(start, newEnd + 1);
		if (newMid.length === 0) return [];

		const changes: GitLineChange[] = [];
		const paired = Math.min(oldMid.length, newMid.length);
		for (let i = 0; i < paired; i++) {
			if (oldMid[i] !== newMid[i]) {
				changes.push({ line: start + i + 1, type: 'modified' });
			}
		}

		for (let i = paired; i < newMid.length; i++) {
			changes.push({ line: start + i + 1, type: 'added' });
		}

		return changes;
	}

	function baseContentFromDiff(currentContent: string, files: DiffFileEntry[]): string {
		const currentLines = splitLines(currentContent);
		if (files.length === 0) return currentContent;

		const baseLines: string[] = [];
		let currentIndex = 0;

		for (const file of files) {
			for (const hunk of file.hunks) {
				const { newStart } = diffHunkStart(hunk.header);
				while (currentIndex < newStart - 1 && currentIndex < currentLines.length) {
					baseLines.push(currentLines[currentIndex++]);
				}

				for (const line of hunk.lines) {
					if (line.type === 'added') {
						currentIndex++;
					} else if (line.type === 'removed') {
						baseLines.push(line.content);
					} else {
						baseLines.push(currentLines[currentIndex++] ?? line.content);
					}
				}
			}
		}

		while (currentIndex < currentLines.length) {
			baseLines.push(currentLines[currentIndex++]);
		}

		return baseLines.join('\n');
	}

	function lineChangesFromDiff(files: DiffFileEntry[]): GitLineChange[] {
		const changes: GitLineChange[] = [];

		for (const file of files) {
			for (const hunk of file.hunks) {
				const lines = diffNumberedLines(hunk);
				let pendingAdded: number[] = [];
				let pendingRemoved = 0;

				function flushBlock() {
					if (pendingAdded.length > 0) {
						pendingAdded.forEach((line, index) => {
							changes.push({ line, type: index < pendingRemoved ? 'modified' : 'added' });
						});
					}
					pendingAdded = [];
					pendingRemoved = 0;
				}

				for (const line of lines) {
					if (line.type === 'context') {
						flushBlock();
					} else if (line.type === 'removed') {
						pendingRemoved++;
					} else if (line.newNumber !== null) {
						pendingAdded.push(line.newNumber);
					}
				}
				flushBlock();
			}
		}

		return changes;
	}

	function relativeGitPath(path: string): string {
		const ws = get(activeWorkspace);
		if (!ws) return path;
		return path.startsWith(ws.path) ? path.slice(ws.path.replace(/\/$/, '').length + 1) : path;
	}

	function gitFileStatus(path: string): GitFile | undefined {
		const status = gitStatusStore.status;
		if (!status?.is_repo) return undefined;
		const relPath = relativeGitPath(path);
		return status.files?.find((f) => f.path === relPath);
	}

	let currentFileStatus = $derived(fileData ? gitFileStatus(fileData.path) : undefined);

	function applyGitLineChanges() {
		gitLineChanges = normalizeGitLineChanges(gitLineChanges);
		view?.dispatch({ effects: setGitLineChangesEffect.of(gitLineChanges) });
	}

	function currentEditorContent(): string {
		return view?.state.doc.toString() ?? fileData?.content ?? '';
	}

	function textLineBreaks(text: string): number {
		return text.split('\n').length - 1;
	}

	function docLineAt(doc: EditorState['doc'], pos: number): number {
		return doc.lineAt(Math.max(0, Math.min(pos, doc.length))).number;
	}

	function applyLiveGitEdit(update: ViewUpdate) {
		if (gitBaseContent === null) return;

		let changes = normalizeGitLineChanges(gitLineChanges);
		update.changes.iterChanges((fromA, toA, fromB, toB, inserted) => {
			const oldStartLine = docLineAt(update.startState.doc, fromA);
			const oldEndLine = docLineAt(update.startState.doc, toA);
			const newStartLine = docLineAt(update.state.doc, fromB);
			const newEndLine = docLineAt(update.state.doc, toB);
			const insertedText = inserted.toString();
			const insertedBreaks = textLineBreaks(insertedText);
			const removedBreaks = oldEndLine - oldStartLine;
			const lineDelta = insertedBreaks - removedBreaks;

			changes = changes
				.map((change) => {
					if (change.line <= oldStartLine) return change;
					if (change.line > oldEndLine) return { ...change, line: change.line + lineDelta };
					return null;
				})
				.filter((change): change is GitLineChange => change !== null);

			if (insertedBreaks > 0) {
				for (let line = newStartLine + 1; line <= newStartLine + insertedBreaks; line++) {
					changes.push({ line, type: 'added' });
				}
			}

			const parts = insertedText.split('\n');
			const changedExistingLine =
				insertedBreaks === 0 ||
				parts[0].length > 0 ||
				(parts.at(-1)?.length ?? 0) > 0 ||
				(removedBreaks === 0 && toA > fromA);

			if (changedExistingLine && newStartLine <= newEndLine) {
				changes.push({ line: newStartLine, type: 'modified' });
			}
		}, true);

		gitLineChanges = changes;
		applyGitLineChanges();
	}

	function gitFilesSignature(files: GitFile[] | undefined): string {
		return (files ?? [])
			.map((f) => `${f.path}:${f.status}:${f.staged ? '1' : '0'}:${f.unstaged ? '1' : '0'}`)
			.join('|');
	}

	async function refreshGitLineChanges(path: string) {
		const seq = ++gitLineRefreshSeq;
		gitLineChanges = [];
		gitBaseContent = null;

		if (
			path.startsWith('untitled:') ||
			isBinaryPreview ||
			fileData?.content === null ||
			fileData?.content === undefined
		) {
			applyGitLineChanges();
			return;
		}

		const ws = get(activeWorkspace);
		if (!ws) {
			applyGitLineChanges();
			return;
		}

		if (!gitStatusStore.status) {
			await gitStatusStore.refresh();
			if (seq !== gitLineRefreshSeq) return;
		}

		const status = gitStatusStore.status;
		const fileStatus = gitFileStatus(path);
		if (!status?.is_repo) {
			hasGitChanges = false;
			applyGitLineChanges();
			return;
		}
		hasGitChanges = !!fileStatus;

		if (!fileStatus) {
			gitBaseContent = fileData.content;
			gitLineChanges = [];
			applyGitLineChanges();
			return;
		}

		try {
			const params = new URLSearchParams({
				root: ws.path,
				file: relativeGitPath(path),
				staged: 'false',
				untracked: String(fileStatus.status === 'untracked')
			});
			const d = (await getGitDiff(params.toString())) as { files?: DiffFileEntry[] };
			if (seq !== gitLineRefreshSeq) return;
			gitBaseContent =
				fileStatus.status === 'untracked'
					? ''
					: baseContentFromDiff(fileData.content, d.files ?? []);
			gitLineChanges = lineChangesFromDiff(d.files ?? []);
		} catch {
			if (seq !== gitLineRefreshSeq) return;
			gitBaseContent = fileData.content;
			gitLineChanges = [];
		}

		applyGitLineChanges();
	}

	// Editing state: true when the code editor should be active
	let isEditing = $derived(
		fileData &&
			!fileData.binary &&
			!isBinaryPreview &&
			!isCsv &&
			(isMarkdown
				? markdownMode === 'raw'
				: isHtml || isSvg
					? !previewMode
					: isJson
						? jsonViewMode === 'source' || jsonParseError
						: true)
	);

	// ── Language support ─────────────────────────────────────────
	function getLanguageExtension(lang: string | null) {
		switch (lang) {
			case 'javascript':
				return javascript();
			case 'typescript':
				return javascript({ typescript: true });
			case 'jsx':
				return javascript({ jsx: true });
			case 'tsx':
				return javascript({ jsx: true, typescript: true });
			case 'python':
				return python();
			case 'json':
				return json();
			case 'css':
				return css();
			case 'html':
				return html();
			case 'svelte':
				return html();
			case 'markdown':
				return markdown();
			case 'rust':
				return rust();
			case 'c':
			case 'cpp':
				return cpp();
			case 'java':
			case 'kotlin':
				return java();
			case 'sql':
				return sql();
			case 'xml':
				return xml();
			case 'yaml':
				return yaml();
			case 'bash':
			case 'shell':
			case 'sh':
			case 'zsh':
				return StreamLanguage.define(shell);
			default:
				return [];
		}
	}

	// ── File loading ─────────────────────────────────────────────
	let loadedPath = '';

	$effect(() => {
		if (filePath && filePath !== loadedPath) {
			loadedPath = filePath;
			if (filePath.startsWith('untitled:')) {
				initUntitledFile();
			} else {
				loadFile(filePath);
			}
		}
	});

	$effect(() => {
		const wsPath = $activeWorkspace?.path ?? '';
		const status = gitStatusStore.status;
		const data = fileData;
		const content = data?.content;
		if (
			!data ||
			data.binary ||
			isBinaryPreview ||
			isUntitled ||
			content === null ||
			content === undefined
		) {
			return;
		}

		const statusKey = status ? `${status.is_repo}:${gitFilesSignature(status.files)}` : 'pending';
		const key = `${wsPath}:${data.path}:${content.length}:${statusKey}`;
		if (key === lastGitLineRefreshKey) return;
		lastGitLineRefreshKey = key;
		void refreshGitLineChanges(data.path);
	});

	function initUntitledFile() {
		loading = false;
		error = null;
		hasChanges = false;
		saved = false;
		hasGitChanges = false;
		gitLineChanges = [];
		gitBaseContent = null;
		lastGitLineRefreshKey = '';
		fileData = {
			path: filePath,
			name: filePath.replace('untitled:', ''),
			size: 0,
			binary: false,
			content: '',
			language: null
		};
		requestAnimationFrame(() => initEditor('', null));
	}

	async function loadFile(path: string) {
		loading = true;
		error = null;
		hasChanges = false;
		saved = false;
		previewMode = true;
		markdownMode = 'preview';
		jsonViewMode = 'tree';
		jsonData = null;
		jsonParseError = false;
		diffMode = false;
		diffFiles = [];
		hasGitChanges = false;
		gitLineChanges = [];
		lastGitLineRefreshKey = '';
		destroyEditor();
		revokeBinaryUrl();

		try {
			// Binary files: just build the view URL, don't fetch content
			if (isBinaryPreview) {
				binaryUrl = `/api/workspace/files/view?path=${encodeURIComponent(path)}`;
				// Still fetch metadata
				const res = await readFile(path);
				if (res.ok) {
					fileData = await res.json();
				} else {
					fileData = {
						path,
						name: path.split('/').pop() ?? '',
						size: 0,
						binary: true,
						content: null,
						language: null
					};
				}
			} else {
				// Text files
				const res = await readFile(path);
				if (!res.ok) {
					const body = await res.json().catch(() => ({}));
					throw new Error(body.detail || `Error ${res.status}`);
				}
				fileData = await res.json();

				if (fileData && !fileData.binary && fileData.content !== null) {
					if (isMarkdown) {
						// Preview handled by MarkdownRenderer component
						if (edit && !openedInEdit) {
							openedInEdit = true;
							clearTabEdit(tabId);
							requestAnimationFrame(() => {
								enterEditMode();
							});
						}
					} else if (isJson) {
						try {
							jsonData = JSON.parse(fileData.content);
						} catch {
							jsonParseError = true;
							requestAnimationFrame(() => initEditor(fileData!.content!, fileData!.language));
						}
					} else if (isCsv) {
						// Handled by its component, no editor needed
					} else if (isSvg || isHtml) {
						// Start in preview mode, editor created on toggle
						previewMode = true;
					} else {
						requestAnimationFrame(() => initEditor(fileData!.content!, fileData!.language));
					}
				}
			}
		} catch (e: any) {
			error = e.message || 'Failed to load file';
			fileData = null;
		} finally {
			loading = false;
		}

		// Check git status for this file from centralized store (non-blocking)
		if (!isUntitled && !isBinaryPreview) {
			const fileStatus = gitFileStatus(path);
			hasGitChanges = !!fileStatus;
			if (gitStatusStore.status?.is_repo) {
				await refreshGitLineChanges(path);
			}
		}
	}

	function revokeBinaryUrl() {
		// Object URLs would need revoking; query-string URLs don't
		binaryUrl = null;
	}

	function togglePreview() {
		previewMode = !previewMode;
		if (!previewMode && fileData?.content !== null) {
			requestAnimationFrame(() => initEditor(fileData!.content!, fileData!.language));
		} else if (previewMode) {
			if (view) {
				const content = view.state.doc.toString();
				fileData!.content = content;
			}
			destroyEditor();
		}
	}

	async function toggleDiff() {
		if (diffMode) {
			diffMode = false;
			diffFiles = [];
			// Restore editor if it was a text file
			if (
				fileData &&
				!fileData.binary &&
				!isBinaryPreview &&
				!isCsv &&
				!(isMarkdown && markdownMode !== 'raw') &&
				!(isHtml || isSvg) &&
				!(isJson && !jsonParseError)
			) {
				requestAnimationFrame(() =>
					initEditor(fileData!.content!, isMarkdown ? 'markdown' : fileData!.language)
				);
			}
			return;
		}

		diffMode = true;
		diffLoading = true;
		destroyEditor();

		try {
			const ws = get(activeWorkspace);
			if (!ws) throw new Error('No workspace');
			const relPath = filePath.startsWith(ws.path)
				? filePath.slice(ws.path.replace(/\/$/, '').length + 1)
				: filePath;
			const fileStatus = gitFileStatus(filePath);
			const params = new URLSearchParams({
				root: ws.path,
				file: relPath,
				staged: 'false',
				untracked: String(fileStatus?.status === 'untracked'),
				ignore_whitespace: String($hideWhitespaceChanges)
			});
			const d = (await getGitDiff(params.toString())) as { files?: DiffFileEntry[] };
			diffFiles = d.files ?? [];
		} catch {
			diffFiles = [];
		} finally {
			diffLoading = false;
			await tick();
			if (diffScrollEl) diffScrollEl.scrollLeft = 0;
		}
	}

	// ── Markdown mode switching ─────────────────────────────────
	async function enterEditMode() {
		if (!RichTextEditor) {
			const mod = await import('./markdown/RichTextEditor.svelte');
			RichTextEditor = mod.default;
		}
		// Sync content from raw mode if needed
		if (markdownMode === 'raw' && view) {
			fileData!.content = view.state.doc.toString();
			destroyEditor();
		}
		markdownMode = 'editor';
	}

	function switchMarkdownMode(target: MarkdownMode) {
		// Capture current content before switching
		if (markdownMode === 'editor' && richTextRef) {
			fileData!.content = richTextRef.getMarkdown();
		} else if (markdownMode === 'raw' && view) {
			fileData!.content = view.state.doc.toString();
			destroyEditor();
		}

		if (target === 'editor') {
			enterEditMode();
			return;
		}

		markdownMode = target;

		if (target === 'raw') {
			requestAnimationFrame(() => initEditor(fileData!.content!, 'markdown'));
		}
	}

	// ── JSON toggle ──────────────────────────────────────────────
	function toggleJsonView() {
		if (jsonViewMode === 'tree') {
			jsonViewMode = 'source';
			requestAnimationFrame(() => initEditor(fileData!.content!, fileData!.language));
		} else {
			jsonViewMode = 'tree';
			if (view) {
				const content = view.state.doc.toString();
				fileData!.content = content;
				try {
					jsonData = JSON.parse(content);
					jsonParseError = false;
				} catch {
					jsonParseError = true;
				}
			}
			destroyEditor();
		}
	}

	// ── CodeMirror ───────────────────────────────────────────────
	function isDarkMode(): boolean {
		return document.documentElement.classList.contains('dark');
	}

	let themeObserver: MutationObserver | null = null;
	let appliedSearchTarget = 0;

	function applySearchTarget() {
		if (!view || !searchTarget || appliedSearchTarget === searchTarget.requestId) return;
		const target = searchTarget;
		const line = view.state.doc.line(Math.min(Math.max(target.line, 1), view.state.doc.lines));
		const from = Math.min(line.from + Math.max(target.column - 1, 0), line.to);
		const to = Math.min(from + Math.max(target.length, 0), line.to);
		view.dispatch({
			selection: { anchor: from, head: to },
			effects: EditorView.scrollIntoView(from, { y: 'center' })
		});
		appliedSearchTarget = target.requestId;
		clearFileSearchTarget(tabId, target.requestId);
	}

	$effect(() => {
		if (searchTarget && fileData && !fileData.binary) {
			if (isMarkdown && markdownMode !== 'raw') switchMarkdownMode('raw');
			else if (isJson && jsonViewMode !== 'source') toggleJsonView();
			else if ((isHtml || isSvg) && previewMode) togglePreview();
			else if (isCsv && !view) {
				requestAnimationFrame(() => initEditor(fileData!.content!, fileData!.language));
			}
		}
		applySearchTarget();
	});

	function initEditor(content: string, language: string | null) {
		if (!editorEl) return;
		destroyEditor();

		const dark = isDarkMode();

		const extensions = [
			lineNumbers(),
			gitLineChangeField,
			highlightActiveLineGutter(),
			highlightSpecialChars(),
			history(),
			foldGutter(),
			drawSelection(),
			indentOnInput(),
			bracketMatching(),
			closeBrackets(),
			autocompletion(),
			EditorState.languageData.of(() => [{ autocomplete: completeAnyWord }]),
			rectangularSelection(),
			highlightActiveLine(),
			highlightSelectionMatches(),
			keymap.of([
				...closeBracketsKeymap,
				...completionKeymap,
				...defaultKeymap,
				...searchKeymap,
				...historyKeymap,
				...foldKeymap,
				indentWithTab,
				{
					key: 'Mod-s',
					run: () => {
						saveFile();
						return true;
					}
				}
			]),
			syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
			...(dark ? [oneDark] : []),
			EditorView.updateListener.of((update) => {
				if (update.docChanged) {
					hasChanges = true;
					saved = false;
					markTabUnsaved(tabId, true);
					applyLiveGitEdit(update);
				}
			}),
			EditorView.theme({
				'&': {
					height: '100%',
					fontSize: '0.8125rem',
					backgroundColor: 'var(--app-bg)',
					color: 'var(--app-fg)'
				},
				'.cm-scroller': { fontFamily: '"JetBrains Mono", "Fira Code", ui-monospace, monospace' },
				'.cm-gutters': {
					backgroundColor: 'var(--app-bg)',
					color: 'var(--app-fg-subtle)',
					border: 'none'
				},
				'.cm-activeLineGutter': { backgroundColor: 'var(--app-hover)' },
				'.cm-activeLine': { backgroundColor: 'var(--app-hover)' },
				'.cm-lineNumbers .cm-gutterElement.cm-git-line-added': {
					boxShadow: `inset 0.125rem 0 0 ${dark ? 'rgba(52, 211, 153, 0.75)' : 'rgba(5, 150, 105, 0.65)'}`
				},
				'.cm-lineNumbers .cm-gutterElement.cm-git-line-modified': {
					boxShadow: `inset 0.125rem 0 0 ${dark ? 'rgba(251, 191, 36, 0.7)' : 'rgba(217, 119, 6, 0.6)'}`
				},
				'&.cm-focused': { outline: 'none' }
			}),
			getLanguageExtension(language)
		];

		const state = EditorState.create({ doc: content, extensions });
		view = new EditorView({ state, parent: editorEl });
		applyGitLineChanges();
		applySearchTarget();

		// Watch for theme changes and rebuild editor
		themeObserver = new MutationObserver(() => {
			if (view) {
				const doc = view.state.doc.toString();
				initEditor(doc, language);
			}
		});
		themeObserver.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ['class']
		});
	}

	function destroyEditor() {
		themeObserver?.disconnect();
		themeObserver = null;
		view?.destroy();
		view = null;
	}

	async function saveFile() {
		if (!fileData) return;

		// For untitled files, show Save As dialog
		if (isUntitled) {
			showSaveDialog = true;
			return;
		}

		await doSaveToPath(fileData.path);
	}

	async function doSaveToPath(path: string) {
		if (!fileData) return;
		saving = true;
		try {
			let content: string;
			if (isMarkdown && markdownMode === 'editor' && richTextRef) {
				content = richTextRef.getMarkdown();
			} else if (view) {
				content = view.state.doc.toString();
			} else {
				content = fileData.content ?? '';
			}
			await writeFile(path, content);
			hasChanges = false;
			saved = true;
			fileData!.content = content;
			fileData!.path = path;
			fileData!.name = path.split('/').pop() || path;
			markTabUnsaved(tabId, false);
			await gitStatusStore.refresh({ force: true });
			await refreshGitLineChanges(path);

			// If this was an untitled file, promote the tab to a real file
			if (filePath.startsWith('untitled:')) {
				updateTabFilePath(tabId, path);
				loadedPath = path;
			}

			setTimeout(() => {
				saved = false;
			}, 2000);
		} catch (e: any) {
			error = e.message;
		} finally {
			saving = false;
		}
	}

	function handleSaveDialogSave(fullPath: string) {
		showSaveDialog = false;
		doSaveToPath(fullPath);
	}

	function formatSize(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / 1048576).toFixed(1)} MB`;
	}

	function gitStatusChar(status: string): string {
		const chars: Record<string, string> = {
			added: 'A',
			untracked: 'U',
			modified: 'M',
			deleted: 'D',
			renamed: 'R'
		};
		return chars[status] ?? '?';
	}

	onDestroy(() => {
		destroyEditor();
		revokeBinaryUrl();
	});
</script>

<div class="editor-container">
	{#if fileData}
		<!-- Universal toolbar (hidden for untitled files with no content yet) -->
		<div class="toolbar">
			<div class="toolbar-left">
				<span class="file-title">
					<span class="file-name scrollbar-none">{fileData.name}</span>
					{#if currentFileStatus}
						<span class="file-git-stats">
							{#if currentFileStatus.binary}
								<span class="file-git-status">{gitStatusChar(currentFileStatus.status)}</span>
							{:else}
								<span class="file-git-additions">+{currentFileStatus.additions ?? 0}</span>
								<span class="file-git-deletions">-{currentFileStatus.deletions ?? 0}</span>
							{/if}
						</span>
					{/if}
				</span>
				<span class="file-size">{formatSize(fileData.size)}</span>
			</div>
			<div class="toolbar-right">
				{#if isMarkdown && !fileData.binary}
					<button
						class="toolbar-btn"
						class:active={markdownMode === 'preview'}
						onclick={() => switchMarkdownMode('preview')}
						use:tooltip={$t('port.preview')}><Icon name="eye" size={12} /></button
					>
					<button
						class="toolbar-btn"
						class:active={markdownMode === 'editor'}
						onclick={() => switchMarkdownMode('editor')}
						use:tooltip={$t('preview.editor')}><Icon name="pencil" size={11} /></button
					>
					<button
						class="toolbar-btn"
						class:active={markdownMode === 'raw'}
						onclick={() => switchMarkdownMode('raw')}
						use:tooltip={$t('preview.source')}><Icon name="code" size={12} /></button
					>
				{:else if (isHtml || isSvg) && !fileData.binary}
					<button
						class="toolbar-btn"
						class:active={previewMode}
						onclick={() => {
							if (!previewMode) togglePreview();
						}}
						use:tooltip={$t('port.preview')}><Icon name="eye" size={12} /></button
					>
					<button
						class="toolbar-btn"
						class:active={!previewMode}
						onclick={() => {
							if (previewMode) togglePreview();
						}}
						use:tooltip={$t('common.edit')}><Icon name="pencil" size={11} /></button
					>
				{/if}
				{#if isJson && !fileData.binary && !jsonParseError}
					<button
						class="toolbar-btn"
						class:active={jsonViewMode === 'tree'}
						onclick={() => {
							if (jsonViewMode !== 'tree') toggleJsonView();
						}}
						use:tooltip={$t('preview.treeView')}><Icon name="list" size={12} /></button
					>
					<button
						class="toolbar-btn"
						class:active={jsonViewMode === 'source'}
						onclick={() => {
							if (jsonViewMode !== 'source') toggleJsonView();
						}}
						use:tooltip={$t('preview.source')}><Icon name="code" size={12} /></button
					>
				{/if}
				{#if isEditing || markdownMode === 'editor' || hasChanges || saving || saved}
					<button
						class="toolbar-btn {saved ? 'saved' : ''}"
						onclick={saveFile}
						disabled={saving}
						use:tooltip={saving ? $t('settings.saving') : saved ? $t('settings.saved') : $t('settings.save')}
						><Icon name={saved ? 'check' : 'save'} size={11} /></button
					>
				{/if}
				{#if !isUntitled && hasGitChanges}
					<button
						class="toolbar-btn"
						class:active={diffMode}
						onclick={toggleDiff}
						use:tooltip={diffMode ? $t('preview.hideDiff') : $t('preview.showDiff')}
						><Icon name="git-diff" size={12} /></button
					>
				{/if}
				{#if !isUntitled}
					<button class="toolbar-btn" onclick={() => loadFile(filePath)} use:tooltip={$t('files.refresh')}
						><Icon name="refresh" size={11} /></button
					>
				{/if}
			</div>
		</div>
	{/if}

	<div class="editor-area">
		{#if loading}
			<div class="state"><Spinner size={20} /></div>
		{:else if error}
			<div class="state error">{error}</div>

			<!-- Diff view -->
		{:else if diffMode}
			{#if diffLoading}
				<div class="state"><Spinner size={20} /></div>
			{:else if diffFiles.length === 0}
				<div class="state">
					<p class="state-title">{$t('editor.noChanges')}</p>
					<p class="state-sub">{$t('editor.noUncommittedModifications')}</p>
				</div>
			{:else}
				<div class="diff-scroll" bind:this={diffScrollEl}>
					<div class="diff-content" class:diff-content-split={$diffDisplayMode === 'split'}>
						{#each diffFiles as df}
							<DiffHunkList hunks={df.hunks} path={df.path} />
						{/each}
					</div>
				</div>
			{/if}

			<!-- ── Binary previews ─────────────────────────────── -->
		{:else if isImage && binaryUrl}
			<ImagePreview src={binaryUrl} alt={fileData?.name ?? ''} />
		{:else if isPdf && binaryUrl}
			<PDFViewer src={binaryUrl} />
		{:else if isVideo && binaryUrl}
			<div class="media-container">
				<!-- svelte-ignore a11y_media_has_caption -->
				<video controls preload="metadata" src={binaryUrl}></video>
			</div>
		{:else if isAudio && binaryUrl}
			<div class="media-container">
				<audio controls preload="metadata" src={binaryUrl}></audio>
			</div>
		{:else if isSqlite && binaryUrl}
			<SqliteView src={binaryUrl} />
		{:else if isOffice && binaryUrl}
			<OfficePreview src={binaryUrl} fileName={fileData?.name ?? ''} />

			<!-- ── Rich text previews ──────────────────────────── -->
		{:else if isSvg && fileData?.content && previewMode}
			<SvgPreview content={fileData.content} />
		{:else if isHtml && fileData?.content && previewMode}
			<HtmlPreview content={fileData.content} {filePath} />
		{:else if isJson && jsonData !== null && jsonViewMode === 'tree'}
			<div class="tree-container">
				<JsonTreeView data={jsonData} />
			</div>
		{:else if isCsv && fileData?.content}
			<CsvTable content={fileData.content} separator={isTsv ? '\t' : ','} />
		{:else if isMarkdown && fileData?.content !== null && fileData?.content !== undefined}
			{#if markdownMode === 'preview'}
				<div class="markdown-preview">
					<MarkdownRenderer content={fileData.content} />
				</div>
			{:else if markdownMode === 'editor' && RichTextEditor}
				{@const wsPath = $activeWorkspace?.path ?? ''}
				<svelte:component
					this={RichTextEditor}
					bind:this={richTextRef}
					content={fileData.content}
					filePath={fileData.path}
					workspacePath={wsPath}
					onchange={() => {
						hasChanges = true;
						saved = false;
						markTabUnsaved(tabId, true);
					}}
					onsave={saveFile}
				/>
			{:else if markdownMode === 'raw'}
				<div bind:this={editorEl} class="editor-el"></div>
			{/if}
		{:else if fileData?.binary}
			<!-- Unknown binary, fallback -->
			<div class="state">
				<p class="state-title">{$t('editor.binaryFile')}</p>
				<p class="state-sub">{fileData.name} ({formatSize(fileData.size)})</p>
			</div>
		{:else}
			<!-- ┌ Code editor (default) ───────────────────── -->
			<div bind:this={editorEl} class="editor-el"></div>
		{/if}

		{#if showSaveDialog}
			<SaveDialog
				defaultName={fileData?.name || 'Untitled'}
				initialDir={(() => {
					const ws = get(activeWorkspace);
					return ws?.path;
				})()}
				onclose={() => (showSaveDialog = false)}
				onsave={handleSaveDialogSave}
			/>
		{/if}
	</div>
</div>

<style>
	@reference "../../app.css";

	.editor-container {
		display: flex;
		flex-direction: column;
		height: 100%;
		overflow: hidden;
	}

	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
		height: 1.875rem;
		padding: 0 0.625rem;
		border-bottom: 1px solid var(--color-gray-200);
		flex-shrink: 0;
	}

	.toolbar {
		border-bottom-color: var(--app-border);
	}

	.toolbar-left {
		display: flex;
		align-items: center;
		flex: 1 1 auto;
		gap: 0.5rem;
		min-width: 0;
		overflow: hidden;
	}

	.toolbar-right {
		display: flex;
		align-items: center;
		flex: 0 0 auto;
		gap: 0.125rem;
	}

	.file-title {
		display: flex;
		align-items: center;
		flex: 1 1 auto;
		gap: 0.375rem;
		min-width: 0;
		overflow: hidden;
	}

	.file-name {
		display: block;
		flex: 0 1 auto;
		min-width: 0;
		max-width: 100%;
		overflow-x: auto;
		overflow-y: hidden;
		white-space: nowrap;
		font-size: 0.75rem;
		font-weight: 500;
		color: var(--color-gray-700);
	}

	.file-name::-webkit-scrollbar {
		display: none;
	}

	:global(.dark) .file-name {
		color: var(--app-fg);
	}

	.file-git-stats {
		display: flex;
		align-items: center;
		flex: 0 0 auto;
		gap: 0.25rem;
		font-family:
			ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
		font-size: 0.6875rem;
		font-weight: 500;
		white-space: nowrap;
	}

	.file-git-additions {
		color: #16a34a;
	}

	.file-git-deletions {
		color: #ef4444;
	}

	.file-git-status {
		color: var(--color-gray-500);
	}

	:global(.dark) .file-git-additions {
		color: #4ade80;
	}

	:global(.dark) .file-git-deletions {
		color: #f87171;
	}

	.file-size {
		flex: 0 0 auto;
		white-space: nowrap;
		font-size: 0.6875rem;
		color: var(--color-gray-400);
	}

	.toolbar-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		flex: 0 0 auto;
		width: 1.25rem;
		height: 1.25rem;
		border-radius: 0.25rem;
		color: var(--color-gray-400);
		transition: all 0.1s;
	}

	@media (max-width: 768px) {
		.toolbar-btn {
			width: 2rem;
			height: 2rem;
			border-radius: 0.375rem;
		}

		.toolbar {
			height: 2.375rem;
		}

		.toolbar-right {
			gap: 0.25rem;
		}
	}

	.toolbar-btn:hover {
		color: var(--color-gray-600);
	}

	:global(.dark) .toolbar-btn:hover {
		color: var(--color-gray-300);
	}

	.toolbar-btn.active {
		color: var(--color-gray-900);
	}

	:global(.dark) .toolbar-btn.active {
		color: var(--app-fg);
	}

	.editor-area {
		flex: 1;
		overflow: hidden;
	}

	.editor-el {
		height: 100%;
		width: 100%;
	}

	/* ── Media containers ────────────────────────────────── */

	.media-container {
		display: flex;
		align-items: center;
		justify-content: center;
		height: 100%;
		padding: 1.5rem;
	}

	.media-container video {
		max-width: 100%;
		max-height: 100%;
		border-radius: 0.5rem;
		background: var(--app-bg);
	}

	.media-container audio {
		width: 100%;
		max-width: 30rem;
	}

	/* ── Tree container ──────────────────────────────────── */

	.tree-container {
		height: 100%;
		overflow: auto;
		padding: 0.5rem 0.75rem;
	}

	/* ── Markdown preview container ──────────────────────── */

	.markdown-preview {
		height: 100%;
		width: 100%;
		overflow-y: auto;
		padding: 1.5rem 2rem;
	}

	/* ── States ──────────────────────────────────────────── */

	.state {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		height: 100%;
		gap: 0.375rem;
	}

	.state.error {
		color: var(--color-gray-400);
		font-size: 0.8125rem;
	}

	.state-title {
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--color-gray-400);
	}

	.state-sub {
		font-size: 0.75rem;
		color: var(--color-gray-500);
	}

	/* ── Diff view ────────────────────────────────────────── */

	.diff-scroll {
		height: 100%;
		overflow: auto;
		font-family: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
		font-size: 0.6875rem;
		line-height: 1.125rem;
	}

	.diff-content {
		width: max-content;
		min-width: 100%;
	}

	.diff-content-split {
		width: 100%;
	}
</style>
