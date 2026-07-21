/**
 * Workspace + Tab state management for cptr.
 *
 * State is split into three layers:
 *   1. Browser-tab state: which workspace is active, determined by URL (?workspace=...)
 *   2. Workspace state: tabs, groups, split layout, persisted server-side per workspace path
 *   3. User preferences: theme, locale, sidebar, persisted server-side globally
 *
 * Architecture:
 *   Workspace → EditorGroup[] (1 or more groups, each with independent tabs)
 *   Each group has its own tab list and active tab, like VS Code editor groups.
 *   Groups are arranged in a nested split tree, like VS Code editor groups.
 *
 * Multiple browser tabs can independently view different workspaces without
 * interfering; each tab reads its workspace path from the URL and loads/saves
 * only that workspace's state.
 */

import { writable, derived, get } from 'svelte/store';
import { toast } from 'svelte-sonner';
import {
	getPreferences,
	savePreferences,
	getWorkspaceList,
	getWorkspaceState,
	saveWorkspaceState
} from '$lib/apis/state';
import { listSessions, createSession, deleteSession } from '$lib/apis/terminal';
import { createBrowserSession, deleteBrowserSession, listBrowserSessions } from '$lib/apis/browser';
import { changeLocale, i18next } from '$lib/i18n';
import { requestConfirm } from '$lib/stores/confirm';
import { streamingChatTabs } from '$lib/stores/chat';
import { keybindings, loadKeybindings } from '$lib/stores/keybindings';
import { defaultPwaPreferences, type PwaPreferences } from '$lib/intents/types';
import { getPathDisplayName, isSupportedWorkspacePath } from '$lib/utils/paths';
import {
	applyAppearance,
	normalizeBorderContrast,
	sanitizeThemeConfig,
	type AppearancePreferences,
	type Theme,
	type ThemeConfig
} from '$lib/utils/appearance';

export type { AppearancePreferences, Theme, ThemeConfig };

// ── Types ───────────────────────────────────────────────────────

export interface FileSearchTarget {
	line: number;
	column: number;
	length: number;
	requestId: number;
}

export interface Tab {
	id: string;
	type: 'home' | 'files' | 'terminal' | 'file' | 'git' | 'chat' | 'preview' | 'browser'; // preview is migrated on load
	label: string;
	filePath?: string;
	edit?: boolean;
	path?: string; // generic path (e.g. for chat)
	sessionId?: string;
	port?: number; // legacy preview port, migrated on load
	browserSessionId?: string;
	unsaved?: boolean;
	permanent?: boolean;
	badge?: number;
	searchTarget?: FileSearchTarget;
}

export type SplitDirection = 'horizontal' | 'vertical';

export interface EditorGroup {
	id: string;
	tabs: Tab[];
	activeTabId: string;
	tabHistory?: string[]; // MRU stack of tab IDs (most recent last)
}

export type EditorLayout = EditorGroupLeaf | EditorSplit;

export interface EditorGroupLeaf {
	type: 'group';
	groupId: string;
}

export interface EditorSplit {
	type: 'split';
	id: string;
	direction: SplitDirection;
	ratio: number;
	first: EditorLayout;
	second: EditorLayout;
}

export interface WorkspaceState {
	name: string;
	path: string;
	groups: EditorGroup[];
	activeGroupId: string;
	layout: EditorLayout;
	splitDirection: SplitDirection;
	splitRatio: number; // 0-1, fraction for the first group
	fileBrowserCwd: string;
}

export interface HomeState {
	groups: EditorGroup[];
	activeGroupId: string;
	layout: EditorLayout;
	splitDirection: SplitDirection;
}

export type ToolApprovalMode = 'ask' | 'auto' | 'full';

export interface UserPreferences {
	theme?: Theme;
	appearance?: AppearancePreferences;
	sidebarOpen: boolean;
	sidebarWidth: number;
	toolApprovalMode?: ToolApprovalMode;
	locale: string;
	workspaceOrder?: string[]; // ordered paths for sidebar drag-reorder
	keybindings?: Record<string, string>; // user-customised keyboard shortcuts
	version?: string; // last seen app version for changelog
	showUpdateToast?: boolean; // show version update notifications (default true)
	pwa?: PwaPreferences;
	textScale?: number | null;
	widescreenMode?: boolean;
	expandToolDetails?: boolean;
	homeGroup?: EditorGroup;
	homeState?: HomeState;
}

// ── ID generation ───────────────────────────────────────────────

let _idCounter = Date.now();
function nextId(): string {
	return (++_idCounter).toString(36);
}

// ── Default group for a workspace ───────────────────────────────

function createDefaultGroup(): EditorGroup {
	return {
		id: 'default',
		tabs: [{ id: 'files', type: 'files', label: 'Files', permanent: true }],
		activeTabId: 'files'
	};
}

function createDefaultWorkspace(path: string): WorkspaceState {
	const name = getPathDisplayName(path);
	return {
		name,
		path,
		groups: [createDefaultGroup()],
		activeGroupId: 'default',
		layout: { type: 'group', groupId: 'default' },
		splitDirection: 'horizontal',
		splitRatio: 0.5,
		fileBrowserCwd: path
	};
}

function splitLayout(
	layout: EditorLayout,
	groupId: string,
	newGroupId: string,
	direction: SplitDirection,
	placement: 'before' | 'after' = 'after'
): EditorLayout {
	if (layout.type === 'group') {
		if (layout.groupId !== groupId) return layout;
		const newLeaf: EditorGroupLeaf = { type: 'group', groupId: newGroupId };
		return {
			type: 'split',
			id: nextId(),
			direction,
			ratio: 0.5,
			first: placement === 'before' ? newLeaf : layout,
			second: placement === 'before' ? layout : newLeaf
		};
	}
	return {
		...layout,
		first: splitLayout(layout.first, groupId, newGroupId, direction, placement),
		second: splitLayout(layout.second, groupId, newGroupId, direction, placement)
	};
}

function replaceLayoutGroup(
	layout: EditorLayout,
	groupId: string,
	replacementId: string
): EditorLayout {
	if (layout.type === 'group') {
		return layout.groupId === groupId ? { type: 'group', groupId: replacementId } : layout;
	}
	return {
		...layout,
		first: replaceLayoutGroup(layout.first, groupId, replacementId),
		second: replaceLayoutGroup(layout.second, groupId, replacementId)
	};
}

function removeLayoutGroup(layout: EditorLayout, groupId: string): EditorLayout | null {
	if (layout.type === 'group') return layout.groupId === groupId ? null : layout;
	const first = removeLayoutGroup(layout.first, groupId);
	const second = removeLayoutGroup(layout.second, groupId);
	if (!first) return second;
	if (!second) return first;
	return { ...layout, first, second };
}

function layoutGroupIds(layout: EditorLayout): string[] {
	return layout.type === 'group'
		? [layout.groupId]
		: [...layoutGroupIds(layout.first), ...layoutGroupIds(layout.second)];
}

function isEditorLayout(value: unknown): value is EditorLayout {
	if (!value || typeof value !== 'object') return false;
	const node = value as Partial<EditorLayout>;
	return node.type === 'group'
		? typeof node.groupId === 'string'
		: node.type === 'split' &&
				typeof node.id === 'string' &&
				(node.direction === 'horizontal' || node.direction === 'vertical') &&
				typeof node.ratio === 'number' &&
				isEditorLayout(node.first) &&
				isEditorLayout(node.second);
}

function createLayout(
	groups: EditorGroup[],
	direction: SplitDirection,
	ratio: number
): EditorLayout {
	let layout: EditorLayout = { type: 'group', groupId: groups[0].id };
	for (const group of groups.slice(1)) {
		layout = {
			type: 'split',
			id: nextId(),
			direction,
			ratio,
			first: layout,
			second: { type: 'group', groupId: group.id }
		};
	}
	return layout;
}

function normalizeLayout(
	layout: unknown,
	groups: EditorGroup[],
	direction: SplitDirection,
	ratio: number
): EditorLayout {
	if (!isEditorLayout(layout)) return createLayout(groups, direction, ratio);
	const ids = layoutGroupIds(layout);
	const groupIds = groups.map((group) => group.id);
	return ids.length === groupIds.length &&
		ids.every((id) => groupIds.includes(id)) &&
		new Set(ids).size === ids.length
		? layout
		: createLayout(groups, direction, ratio);
}

// ── Stores ──────────────────────────────────────────────────────

/** The workspace currently displayed in THIS browser tab. Null = welcome page. */
export const currentWorkspace = writable<WorkspaceState | null>(null);
export const homeState = writable<HomeState>({
	groups: [
		{
			id: 'home',
			tabs: [{ id: 'home', type: 'home', label: 'Home', permanent: true }],
			activeTabId: 'home'
		}
	],
	activeGroupId: 'home',
	layout: { type: 'group', groupId: 'home' },
	splitDirection: 'horizontal'
});

/** List of all workspace summaries for the sidebar. */
export const workspaceList = writable<{ path: string; name: string; unread_count: number }[]>([]);

/** Global user preferences. */
export const sidebarOpen = writable(
	typeof window !== 'undefined' ? window.innerWidth >= 1024 : false
);

// Auto-close sidebar when window is resized below mobile breakpoint
if (typeof window !== 'undefined') {
	window.addEventListener('resize', () => {
		if (window.innerWidth < 768) {
			sidebarOpen.set(false);
		}
	});
}
export const sidebarWidth = writable(220);
export const theme = writable<Theme>('dark');
export const toolApprovalMode = writable<ToolApprovalMode>('auto');
export const appVersion = writable('');
export const lastSeenVersion = writable('');
export const latestVersion = writable('');
export const updateAvailable = derived([appVersion, latestVersion], ([$app, $latest]) => {
	if (!$app || !$latest || $app === 'dev' || $app === '0.0.0') return false;
	return (
		$app.localeCompare($latest, undefined, {
			numeric: true,
			sensitivity: 'case',
			caseFirst: 'upper'
		}) < 0
	);
});
export const showChangelog = writable(false);
export const showUpdateToastPref = writable(true);
export const showSearch = writable(false);
export const stateLoaded = writable(false);
export const gitReviewOpen = writable(false);
export const isGitRepo = writable(false);
export type StreamingBehavior = 'queue' | 'interrupt';
export const streamingBehavior = writable<StreamingBehavior>('queue');
export const pwaPreferences = writable<PwaPreferences>(defaultPwaPreferences);
export const themeConfig = writable<ThemeConfig | null>(null);
export const textScale = writable<number | null>(null);
export const borderContrast = writable<number | null>(null);
export const widescreenMode = writable(false);
export const expandToolDetails = writable(false);

/** Saved workspace path order for sidebar drag-reorder. */
export const workspaceOrder = writable<string[]>([]);

// ── Derived stores ──────────────────────────────────────────────

/** @deprecated Alias for currentWorkspace. Helps migration of existing imports */
export const activeWorkspace = currentWorkspace;

export const activeGroup = derived(currentWorkspace, ($ws) =>
	$ws ? ($ws.groups.find((g) => g.id === $ws.activeGroupId) ?? $ws.groups[0] ?? null) : null
);

export const activeTab = derived(activeGroup, ($g) =>
	$g ? ($g.tabs.find((t) => t.id === $g.activeTabId) ?? null) : null
);

export const activeHomeGroup = derived(
	homeState,
	($state) =>
		$state.groups.find((group) => group.id === $state.activeGroupId) ?? $state.groups[0] ?? null
);

export const activeHomeTab = derived(activeHomeGroup, ($group) =>
	$group ? ($group.tabs.find((tab) => tab.id === $group.activeTabId) ?? null) : null
);

export const splitActive = derived(currentWorkspace, ($ws) => ($ws?.groups.length ?? 0) > 1);

// ── Compat aliases for old split-pane API ──────────────────────

/** @deprecated Use splitActive */
export const splitPaneOpen = splitActive;

/** @deprecated The "split tab" is now the active tab of the second group */
export const splitTab = derived(currentWorkspace, ($ws) => {
	if (!$ws || $ws.groups.length < 2) return null;
	const secondGroup = $ws.groups.find((g) => g.id !== $ws.activeGroupId) ?? $ws.groups[1];
	return secondGroup?.tabs.find((t) => t.id === secondGroup.activeTabId) ?? null;
});

/** @deprecated Use closeGroup */
export function closeSplitPane(): void {
	const ws = get(currentWorkspace);
	if (!ws || ws.groups.length < 2) return;
	for (const g of ws.groups.slice(1)) {
		closeGroup(g.id);
	}
}

/** @deprecated No direct equivalent. Swaps active group focus */
export function swapSplitPanes(): void {
	const ws = get(currentWorkspace);
	if (!ws || ws.groups.length < 2) return;
	const otherGroup = ws.groups.find((g) => g.id !== ws.activeGroupId);
	if (otherGroup) setActiveGroup(otherGroup.id);
}

/** @deprecated Use activeGroup */
export const focusedPane = derived(currentWorkspace, ($ws) =>
	$ws?.activeGroupId === $ws?.groups[0]?.id ? 'main' : 'split'
);

// ── MRU tab history helpers ─────────────────────────────────────

function pushTabHistory(group: EditorGroup, tabId: string): string[] {
	const history = (group.tabHistory ?? []).filter((id) => id !== tabId);
	history.push(tabId);
	if (history.length > 50) history.splice(0, history.length - 50);
	return history;
}

// ── Server-side persistence ─────────────────────────────────────

let _saveWsTimer: ReturnType<typeof setTimeout> | null = null;
let _savePrefTimer: ReturnType<typeof setTimeout> | null = null;

function persistWorkspace(): void {
	if (_saveWsTimer) clearTimeout(_saveWsTimer);
	_saveWsTimer = setTimeout(() => {
		const ws = get(currentWorkspace);
		if (!ws) return;
		saveWorkspaceState(ws.path, ws as unknown as Record<string, unknown>).catch(() => {});
	}, 300);
}

function persistPreferences(): void {
	if (_savePrefTimer) clearTimeout(_savePrefTimer);
	_savePrefTimer = setTimeout(() => {
		const prefs: UserPreferences = {
			theme: get(theme),
			appearance: {
				theme: get(theme),
				themeConfig: sanitizeThemeConfig(get(themeConfig)),
				textScale: get(textScale),
				borderContrast: get(borderContrast)
			},
			sidebarOpen: get(sidebarOpen),
			sidebarWidth: get(sidebarWidth),
			toolApprovalMode: get(toolApprovalMode),
			locale: i18next.language,
			workspaceOrder: get(workspaceOrder),
			keybindings: get(keybindings),
			version: get(lastSeenVersion),
			showUpdateToast: get(showUpdateToastPref),
			pwa: get(pwaPreferences),
			textScale: get(textScale),
			widescreenMode: get(widescreenMode),
			expandToolDetails: get(expandToolDetails),
			homeState: get(homeState)
		};
		savePreferences(prefs as unknown as Record<string, unknown>).catch(() => {});
	}, 300);
}

let _subscribed = false;
function subscribeForPersistence() {
	if (_subscribed) return;
	_subscribed = true;
	currentWorkspace.subscribe(() => {
		if (get(stateLoaded)) persistWorkspace();
	});
	homeState.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	theme.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	themeConfig.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	sidebarOpen.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	sidebarWidth.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	toolApprovalMode.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	workspaceOrder.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	keybindings.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	lastSeenVersion.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	showUpdateToastPref.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	pwaPreferences.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	textScale.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	borderContrast.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	widescreenMode.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	expandToolDetails.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	i18next.on('languageChanged', () => {
		if (get(stateLoaded)) persistPreferences();
	});
}

// ── Load preferences (called once at app startup) ───────────────

export async function loadPreferences(): Promise<void> {
	try {
		const prefs = await getPreferences();
		const appearance = (
			prefs.appearance && typeof prefs.appearance === 'object' ? prefs.appearance : {}
		) as AppearancePreferences;
		if (appearance.theme || prefs.theme) theme.set((appearance.theme ?? prefs.theme) as Theme);
		themeConfig.set(sanitizeThemeConfig(appearance.themeConfig));
		if (prefs.sidebarOpen !== undefined) sidebarOpen.set(prefs.sidebarOpen as boolean);
		if (prefs.sidebarWidth !== undefined) sidebarWidth.set(prefs.sidebarWidth as number);
		if (
			prefs.toolApprovalMode === 'ask' ||
			prefs.toolApprovalMode === 'auto' ||
			prefs.toolApprovalMode === 'full'
		) {
			toolApprovalMode.set(prefs.toolApprovalMode);
		} else if (prefs.autoApproveTools !== undefined) {
			toolApprovalMode.set((prefs.autoApproveTools as boolean) ? 'full' : 'ask');
		}
		if (prefs.locale) changeLocale(prefs.locale as string);
		if (Array.isArray(prefs.workspaceOrder)) workspaceOrder.set(prefs.workspaceOrder as string[]);
		if (prefs.keybindings) loadKeybindings(prefs.keybindings as Record<string, string>);
		if (prefs.version) lastSeenVersion.set(prefs.version as string);
		if (prefs.showUpdateToast !== undefined)
			showUpdateToastPref.set(prefs.showUpdateToast as boolean);
		textScale.set(
			typeof appearance.textScale === 'number'
				? appearance.textScale
				: typeof prefs.textScale === 'number'
					? (prefs.textScale as number)
					: null
		);
		borderContrast.set(
			normalizeBorderContrast(appearance.borderContrast) ??
				(appearance.highContrastBorders === true ? 12 : null)
		);
		if (prefs.widescreenMode !== undefined) widescreenMode.set(prefs.widescreenMode as boolean);
		if (prefs.expandToolDetails !== undefined)
			expandToolDetails.set(prefs.expandToolDetails as boolean);
		const savedHomeGroup = prefs.homeGroup as EditorGroup | undefined;
		const savedHomeState =
			(prefs.homeState as HomeState | undefined) ??
			(savedHomeGroup
				? {
						groups: [savedHomeGroup],
						activeGroupId: savedHomeGroup.id,
						layout: { type: 'group' as const, groupId: savedHomeGroup.id },
						splitDirection: 'horizontal' as const
					}
				: undefined);
		if (savedHomeState && Array.isArray(savedHomeState.groups)) {
			const [terminalIds, browserIds] = await Promise.all([
				listSessions().catch(() => []),
				listBrowserSessions().catch(() => [])
			]);
			const aliveTerminals = new Set(terminalIds);
			const aliveBrowsers = new Set(browserIds);
			let groups = savedHomeState.groups
				.map((group) => {
					const tabs = group.tabs.filter(
						(tab) =>
							tab.type !== 'terminal' ||
							(tab.sessionId !== undefined && aliveTerminals.has(tab.sessionId))
					);
					const liveTabs = tabs.filter(
						(tab) =>
							tab.type !== 'browser' ||
							(tab.browserSessionId !== undefined && aliveBrowsers.has(tab.browserSessionId))
					);
					return {
						...group,
						tabs: liveTabs,
						activeTabId: liveTabs.some((tab) => tab.id === group.activeTabId)
							? group.activeTabId
							: (liveTabs[0]?.id ?? '')
					};
				})
				.filter((group) => group.tabs.length > 0);
			if (!groups.length) {
				groups = [
					{
						id: 'home',
						tabs: [{ id: 'home', type: 'home', label: 'Home', permanent: true }],
						activeTabId: 'home'
					}
				];
			}
			if (!groups.some((group) => group.tabs.some((tab) => tab.type === 'home'))) {
				groups[0] = {
					...groups[0],
					tabs: [{ id: 'home', type: 'home', label: 'Home', permanent: true }, ...groups[0].tabs]
				};
			}
			homeState.set({
				groups,
				activeGroupId: groups.some((group) => group.id === savedHomeState.activeGroupId)
					? savedHomeState.activeGroupId
					: groups[0].id,
				layout: normalizeLayout(
					savedHomeState.layout,
					groups,
					savedHomeState.splitDirection ?? 'horizontal',
					0.5
				),
				splitDirection: savedHomeState.splitDirection ?? 'horizontal'
			});
		}
		const pwaPrefs = prefs.pwa;
		if (pwaPrefs)
			pwaPreferences.set({
				...defaultPwaPreferences,
				...(pwaPrefs as PwaPreferences)
			});
	} catch {
		// First run, no preferences yet
	}
}

// ── Load workspace list (called once at app startup) ────────────

export async function loadWorkspaceList(): Promise<void> {
	try {
		const list = await getWorkspaceList();
		const order = get(workspaceOrder);
		if (order.length > 0 && list) {
			// Sort by saved order; unknown paths go to end
			const orderMap = new Map(order.map((p, i) => [p, i]));
			list.sort((a, b) => (orderMap.get(a.path) ?? Infinity) - (orderMap.get(b.path) ?? Infinity));
		}
		workspaceList.set(list || []);
	} catch {
		workspaceList.set([]);
	}
}

// ── Load a specific workspace (called when URL changes) ─────────

export async function loadWorkspace(path: string): Promise<void> {
	if (!isSupportedWorkspacePath(path)) {
		currentWorkspace.set(null);
		return;
	}
	try {
		const wsData = await getWorkspaceState(path);
		const canonicalWorkspacePath = typeof wsData.path === 'string' ? wsData.path : path;

		if (wsData && wsData.groups && (wsData.groups as EditorGroup[]).length > 0) {
			// Validate terminal sessions are still alive
			let aliveSessions: Set<string> = new Set();
			let aliveBrowserSessions: Set<string> = new Set();
			try {
				const sessions = await listSessions();
				aliveSessions = new Set(sessions.map((s) => s.session_id));
			} catch {}
			try {
				aliveBrowserSessions = new Set(await listBrowserSessions());
			} catch {}

			const ws = wsData as unknown as WorkspaceState;
			ws.groups = await Promise.all(
				ws.groups.map(async (group) => ({
					...group,
					tabs: (
						await Promise.all(
							group.tabs.map(async (tab) => {
								if (tab.type !== 'preview' || !tab.port) return tab;
								try {
									const previewUrl = `http://localhost:${tab.port}/`;
									const session = await createBrowserSession(previewUrl);
									aliveBrowserSessions.add(session.session_id);
									const { port, ...browserTab } = tab;
									return {
										...browserTab,
										type: 'browser' as const,
										label: `localhost:${port}`,
										browserSessionId: session.session_id,
										path: previewUrl
									};
								} catch {
									return null;
								}
							})
						)
					).filter((tab): tab is Tab => tab !== null)
				}))
			);

			// Remove dead terminal tabs from all groups
			const cleanedGroups = ws.groups
				.map((g) => {
					const filteredTabs = g.tabs.filter((t: Tab) => {
						if (t.type === 'terminal' && t.sessionId && !aliveSessions.has(t.sessionId)) {
							return false;
						}
						if (
							t.type === 'browser' &&
							(!t.browserSessionId || !aliveBrowserSessions.has(t.browserSessionId))
						) {
							return false;
						}
						return true;
					});
					const activeStillExists = filteredTabs.some((t: Tab) => t.id === g.activeTabId);
					return {
						...g,
						tabs: filteredTabs,
						activeTabId: activeStillExists ? g.activeTabId : (filteredTabs[0]?.id ?? 'files')
					};
				})
				.filter((g) => g.tabs.length > 0);

			const groups = cleanedGroups.length > 0 ? cleanedGroups : [createDefaultGroup()];
			const activeGroupId = groups.some((g) => g.id === ws.activeGroupId)
				? ws.activeGroupId
				: (groups[0]?.id ?? 'default');

			currentWorkspace.set({
				...ws,
				path: canonicalWorkspacePath,
				groups,
				activeGroupId,
				layout: normalizeLayout(
					ws.layout,
					groups,
					ws.splitDirection ?? 'horizontal',
					ws.splitRatio ?? 0.5
				),
				splitDirection: ws.splitDirection ?? 'horizontal',
				splitRatio: ws.splitRatio ?? 0.5,
				fileBrowserCwd: ws.fileBrowserCwd ?? canonicalWorkspacePath
			});
		} else {
			// First time opening this workspace, create defaults
			currentWorkspace.set(createDefaultWorkspace(canonicalWorkspacePath));
		}
	} catch {
		currentWorkspace.set(null);
	}
}

// ── Initialize everything (called once at app startup) ──────────

export async function initState(): Promise<void> {
	await loadPreferences();
	await loadWorkspaceList();
	stateLoaded.set(true);
	subscribeForPersistence();
}

/** @deprecated Use initState */
export const loadStateFromServer = initState;

// ── Appearance application ──────────────────────────────────────

function applyCurrentAppearance() {
	applyAppearance(get(theme), get(themeConfig), get(textScale), get(borderContrast));
}

theme.subscribe(applyCurrentAppearance);
themeConfig.subscribe(applyCurrentAppearance);
textScale.subscribe(applyCurrentAppearance);
borderContrast.subscribe(applyCurrentAppearance);

if (typeof window !== 'undefined') {
	window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
		if (get(theme) === 'system') applyCurrentAppearance();
	});
}

// ── Cross-tab state sync (BroadcastChannel) ─────────────────────
// Syncs theme and locale changes between browser tabs / PWA windows
// so the user doesn't see divergent state.

if (typeof BroadcastChannel !== 'undefined') {
	const channel = new BroadcastChannel('cptr-sync');

	let _syncingFromBroadcast = false;

	channel.onmessage = (e: MessageEvent) => {
		const { type, value } = e.data || {};
		_syncingFromBroadcast = true;
		try {
			if (type === 'theme' && value) {
				theme.set(value);
			} else if (type === 'appearance' && value) {
				if (value.theme) theme.set(value.theme);
				themeConfig.set(sanitizeThemeConfig(value.themeConfig));
				textScale.set(typeof value.textScale === 'number' ? value.textScale : null);
				borderContrast.set(
					normalizeBorderContrast(value.borderContrast) ??
						(value.highContrastBorders === true ? 12 : null)
				);
			} else if (type === 'locale' && value) {
				changeLocale(value);
			}
		} finally {
			_syncingFromBroadcast = false;
		}
	};

	theme.subscribe((t) => {
		if (!_syncingFromBroadcast) {
			channel.postMessage({ type: 'theme', value: t });
			channel.postMessage({
				type: 'appearance',
				value: {
					theme: t,
					themeConfig: get(themeConfig),
					textScale: get(textScale),
					borderContrast: get(borderContrast)
				}
			});
		}
	});

	themeConfig.subscribe((config) => {
		if (!_syncingFromBroadcast) {
			channel.postMessage({
				type: 'appearance',
				value: {
					theme: get(theme),
					themeConfig: config,
					textScale: get(textScale),
					borderContrast: get(borderContrast)
				}
			});
		}
	});

	textScale.subscribe((scale) => {
		if (!_syncingFromBroadcast) {
			channel.postMessage({
				type: 'appearance',
				value: {
					theme: get(theme),
					themeConfig: get(themeConfig),
					textScale: scale,
					borderContrast: get(borderContrast)
				}
			});
		}
	});

	borderContrast.subscribe((contrast) => {
		if (!_syncingFromBroadcast) {
			channel.postMessage({
				type: 'appearance',
				value: {
					theme: get(theme),
					themeConfig: get(themeConfig),
					textScale: get(textScale),
					borderContrast: contrast
				}
			});
		}
	});

	// Locale changes are broadcast from changeLocale() calls;
	// subscribe to i18next language changes.
	if (i18next) {
		i18next.on('languageChanged', (lng: string) => {
			if (!_syncingFromBroadcast) {
				channel.postMessage({ type: 'locale', value: lng });
			}
		});
	}
}

// ── Workspace actions ───────────────────────────────────────────

/**
 * Register a workspace path in the sidebar list.
 * Does NOT set currentWorkspace. That happens when the URL changes
 * (via goto() in the calling component), which triggers loadWorkspace().
 * The URL is the single source of truth for which workspace is active.
 */
export function addWorkspace(path: string): void {
	const name = getPathDisplayName(path, path);

	// Update workspace list for sidebar
	workspaceList.update((list) => {
		if (list.some((w) => w.path === path)) return list;
		return [...list, { path, name, unread_count: 0 }];
	});

	// Append to order
	workspaceOrder.update((order) => {
		if (order.includes(path)) return order;
		return [...order, path];
	});
}

export async function removeWorkspace(path: string): Promise<void> {
	const { deleteWorkspace: deleteWs } = await import('$lib/apis/state');
	await deleteWs(path);
	workspaceList.update((list) => list.filter((w) => w.path !== path));
	workspaceOrder.update((order) => order.filter((p) => p !== path));

	// If this was the current workspace, clear it
	const ws = get(currentWorkspace);
	if (ws?.path === path) {
		currentWorkspace.set(null);
	}
}

/** Reorder workspaces in the sidebar (from drag-and-drop). */
export function reorderWorkspaces(oldIndex: number, newIndex: number): void {
	workspaceList.update((list) => {
		const reordered = [...list];
		const [moved] = reordered.splice(oldIndex, 1);
		reordered.splice(newIndex, 0, moved);
		// Sync order preference
		workspaceOrder.set(reordered.map((w) => w.path));
		return reordered;
	});
}

export function updateWorkspace(partial: Partial<WorkspaceState>): void {
	currentWorkspace.update((ws) => {
		if (!ws) return ws;
		return { ...ws, ...partial };
	});
}

// ── Internal: update a group's tabs ─────────────────────────────

function updateGroupTabs(
	groupId: string | undefined,
	fn: (
		tabs: Tab[],
		group: EditorGroup
	) => { tabs: Tab[]; activeTabId?: string; tabHistory?: string[] }
): void {
	currentWorkspace.update((ws) => {
		if (!ws) return ws;
		const gid = groupId ?? ws.activeGroupId;
		return {
			...ws,
			groups: ws.groups.map((g) => {
				if (g.id !== gid) return g;
				const result = fn(g.tabs, g);
				const newActiveId = result.activeTabId ?? g.activeTabId;
				const tabHistory =
					result.tabHistory ??
					(newActiveId !== g.activeTabId ? pushTabHistory(g, g.activeTabId) : g.tabHistory);
				return { ...g, tabs: result.tabs, activeTabId: newActiveId, tabHistory };
			})
		};
	});
}

// ── Tab actions (operate on the active group by default) ────────

export function reorderTabs(oldIndex: number, newIndex: number, groupId?: string): void {
	updateGroupTabs(groupId, (tabs) => {
		const reordered = [...tabs];
		const [moved] = reordered.splice(oldIndex, 1);
		reordered.splice(newIndex, 0, moved);
		return { tabs: reordered };
	});
}

export function updateTabLabel(tabId: string, label: string): void {
	const value = label.trim().slice(0, 120);
	if (!value) return;
	currentWorkspace.update((workspace) =>
		workspace
			? {
					...workspace,
					groups: workspace.groups.map((group) => ({
						...group,
						tabs: group.tabs.map((tab) => (tab.id === tabId ? { ...tab, label: value } : tab))
					}))
				}
			: workspace
	);
}

export function clearFileSearchTarget(tabId: string, requestId: number): void {
	currentWorkspace.update((workspace) =>
		workspace
			? {
					...workspace,
					groups: workspace.groups.map((group) => ({
						...group,
						tabs: group.tabs.map((tab) =>
							tab.id === tabId && tab.searchTarget?.requestId === requestId
								? { ...tab, searchTarget: undefined }
								: tab
						)
					}))
				}
			: workspace
	);
}

export function openFileTab(
	filePath: string,
	targetGroupId?: string,
	options: { edit?: boolean; searchTarget?: FileSearchTarget } = {}
): void {
	const ws = get(currentWorkspace);
	if (!ws) {
		openHomeFileTab(filePath, targetGroupId, options);
		return;
	}

	const gid = targetGroupId ?? ws.activeGroupId;
	const group = ws.groups.find((g) => g.id === gid);
	if (!group) return;

	// Reuse existing tab within this group
	const existing = group.tabs.find((t) => t.type === 'file' && t.filePath === filePath);
	if (existing) {
		if (options.edit || options.searchTarget) {
			updateGroupTabs(gid, (tabs) => ({
				tabs: tabs.map((t) =>
					t.id === existing.id
						? { ...t, ...(options.edit ? { edit: true } : {}), searchTarget: options.searchTarget }
						: t
				),
				activeTabId: existing.id
			}));
			return;
		}
		setActiveTab(existing.id, gid);
		return;
	}

	const name = getPathDisplayName(filePath, filePath);
	const newTab: Tab = {
		id: nextId(),
		type: 'file',
		label: name,
		filePath,
		edit: options.edit,
		searchTarget: options.searchTarget
	};

	updateGroupTabs(gid, (tabs) => ({
		tabs: [...tabs, newTab],
		activeTabId: newTab.id
	}));
}

function openHomeFileTab(
	filePath: string,
	targetGroupId?: string,
	options: { edit?: boolean; searchTarget?: FileSearchTarget } = {}
): void {
	const state = get(homeState);
	const groupId = targetGroupId ?? state.activeGroupId;
	const group = state.groups.find((item) => item.id === groupId);
	if (!group) return;

	const existing = group.tabs.find((tab) => tab.type === 'file' && tab.filePath === filePath);
	const tab: Tab = existing ?? {
		id: nextId(),
		type: 'file',
		label: getPathDisplayName(filePath, filePath),
		filePath,
		edit: options.edit,
		searchTarget: options.searchTarget
	};
	const updateGroup = (group: EditorGroup) =>
		group.id === groupId
			? { ...group, tabs: existing ? group.tabs : [...group.tabs, tab], activeTabId: tab.id }
			: group;

	homeState.update((current) => ({
		...current,
		activeGroupId: groupId,
		groups: current.groups.map(updateGroup)
	}));
}

export function openUntitledFileTab(targetGroupId?: string): void {
	// Find the lowest unused number across ALL groups
	const ws = get(currentWorkspace);
	const allTabs = ws ? ws.groups.flatMap((g) => g.tabs) : [];
	const usedNumbers = new Set(
		allTabs
			.filter((t) => t.filePath?.startsWith('untitled:Untitled-'))
			.map((t) => parseInt(t.filePath!.replace('untitled:Untitled-', ''), 10))
			.filter((n) => !isNaN(n))
	);
	let n = 1;
	while (usedNumbers.has(n)) n++;

	const label = `Untitled-${n}`;
	const newTab: Tab = {
		id: nextId(),
		type: 'file',
		label,
		filePath: `untitled:${label}`,
		unsaved: true
	};

	updateGroupTabs(targetGroupId, (tabs) => ({
		tabs: [...tabs, newTab],
		activeTabId: newTab.id
	}));
}

export async function openTerminalTab(targetGroupId?: string): Promise<void> {
	const ws = get(currentWorkspace);
	if (!ws) return;

	try {
		const data = await createSession(ws.path);

		const newTab: Tab = {
			id: nextId(),
			type: 'terminal',
			label: 'Terminal',
			sessionId: data.session_id
		};

		updateGroupTabs(targetGroupId, (tabs) => ({
			tabs: [...tabs, newTab],
			activeTabId: newTab.id
		}));
	} catch (e) {
		console.error('Failed to create terminal:', e);
	}
}

export async function openPreviewTab(port: number, targetGroupId?: string): Promise<void> {
	const ws = get(currentWorkspace);
	if (!ws) return;

	const gid = targetGroupId ?? ws.activeGroupId;
	const group = ws.groups.find((g) => g.id === gid);
	if (!group) return;

	// Reuse existing tab within this group
	const url = `http://localhost:${port}/`;
	const existing = group.tabs.find((t) => t.type === 'browser' && t.path === url);
	if (existing) {
		setActiveTab(existing.id, gid);
		return;
	}

	await openBrowserTab(gid, url, `localhost:${port}`);
}

export async function openBrowserTab(
	targetGroupId?: string,
	url?: string,
	label = 'Browser'
): Promise<void> {
	const ws = get(currentWorkspace);
	if (!ws) return;
	const gid = targetGroupId ?? ws.activeGroupId;
	if (!ws.groups.some((group) => group.id === gid)) return;
	const tabId = nextId();
	const pendingTab: Tab = { id: tabId, type: 'browser', label, path: url };
	updateGroupTabs(gid, (tabs) => ({ tabs: [...tabs, pendingTab], activeTabId: tabId }));
	try {
		const session = await createBrowserSession(url);
		let attached = false;
		updateGroupTabs(gid, (tabs) => ({
			tabs: tabs.map((tab) => {
				if (tab.id !== tabId) return tab;
				attached = true;
				return { ...tab, browserSessionId: session.session_id };
			})
		}));
		if (!attached) deleteBrowserSession(session.session_id);
	} catch (error) {
		console.error('Failed to create browser session:', error);
		toast.error(error instanceof Error ? error.message : 'Failed to open Browser');
		closeTab(tabId, gid, { skipUnsavedPrompt: true });
	}
}

export function openChatTab(chatId?: string, targetGroupId?: string): void {
	const ws = get(currentWorkspace);
	if (!ws) return;

	const gid = targetGroupId ?? ws.activeGroupId;
	const group = ws.groups.find((g) => g.id === gid);
	if (!group) return;

	// If chatId provided, reuse existing tab
	if (chatId) {
		const existing = group.tabs.find((t) => t.type === 'chat' && t.path === chatId);
		if (existing) {
			setActiveTab(existing.id, gid);
			return;
		}
	} else {
		// No chatId — reuse an existing new/pending chat tab if one is open
		const existing = group.tabs.find(
			(t) => t.type === 'chat' && (t.path?.startsWith('new-') || t.path?.startsWith('pending-'))
		);
		if (existing) {
			setActiveTab(existing.id, gid);
			return;
		}
	}

	const newTab: Tab = {
		id: nextId(),
		type: 'chat',
		label: chatId ? 'Chat' : 'New Chat',
		path: chatId || `new-${Date.now()}`
	};

	updateGroupTabs(gid, (tabs) => ({
		tabs: [...tabs, newTab],
		activeTabId: newTab.id
	}));
}

export async function closeTab(
	tabId: string,
	groupId?: string,
	options: { skipUnsavedPrompt?: boolean } = {}
): Promise<boolean> {
	const ws = get(currentWorkspace);
	if (!ws) return false;

	// Find the group containing this tab
	const gid =
		groupId ?? ws.groups.find((g) => g.tabs.some((t) => t.id === tabId))?.id ?? ws.activeGroupId;
	const group = ws.groups.find((g) => g.id === gid);
	if (!group) return false;

	const tab = group.tabs.find((t) => t.id === tabId);
	if (!tab || tab.permanent) return false;

	if (tab.unsaved && !options.skipUnsavedPrompt) {
		const confirmed = await requestConfirm({
			title: i18next.t('editor.closeUnsavedTitle', {
				defaultValue: 'Unsaved changes'
			}),
			message: i18next.t('editor.closeUnsavedConfirm', {
				name: tab.label,
				defaultValue: 'Close "{{name}}" without saving changes?'
			}),
			cancelLabel: i18next.t('common.cancel', { defaultValue: 'Cancel' }),
			confirmLabel: i18next.t('editor.closeWithoutSaving', {
				defaultValue: 'Close without saving'
			})
		});
		if (!confirmed) return false;
	}

	if (tab.type === 'terminal' && tab.sessionId) {
		deleteSession(tab.sessionId);
	}
	if (tab.type === 'browser' && tab.browserSessionId) {
		deleteBrowserSession(tab.browserSessionId);
	}

	// Clean up streaming indicator for closed chat tabs
	if (tab.type === 'chat') {
		streamingChatTabs.update((s) => {
			const n = new Set(s);
			n.delete(tabId);
			return n;
		});
	}

	currentWorkspace.update((ws) => {
		if (!ws) return ws;

		let newGroups = ws.groups.map((g) => {
			if (g.id !== gid) return g;
			const newTabs = g.tabs.filter((t) => t.id !== tabId);
			const tabIdSet = new Set(newTabs.map((t) => t.id));
			let newActiveId = g.activeTabId;

			if (newActiveId === tabId) {
				// Walk back through MRU history
				const history = g.tabHistory ?? [];
				let found = false;
				for (let i = history.length - 1; i >= 0; i--) {
					if (tabIdSet.has(history[i])) {
						newActiveId = history[i];
						found = true;
						break;
					}
				}
				if (!found) {
					const idx = g.tabs.findIndex((t) => t.id === tabId);
					newActiveId = newTabs[Math.max(0, idx - 1)]?.id ?? newTabs[0]?.id ?? '';
				}
			}
			const tabHistory = (g.tabHistory ?? []).filter((id) => id !== tabId);
			return { ...g, tabs: newTabs, activeTabId: newActiveId, tabHistory };
		});

		// Remove empty non-primary groups (groups with no tabs collapse)
		newGroups = newGroups.filter((g) => g.tabs.length > 0);
		if (newGroups.length === 0) {
			newGroups = [createDefaultGroup()];
		}

		// If active group was removed, switch to first remaining
		const activeGroupStillExists = newGroups.some((g) => g.id === ws.activeGroupId);
		const closedGroupStillExists = newGroups.some((g) => g.id === gid);

		return {
			...ws,
			groups: newGroups,
			layout: closedGroupStillExists
				? ws.layout
				: (removeLayoutGroup(ws.layout, gid) ?? { type: 'group', groupId: newGroups[0].id }),
			activeGroupId: activeGroupStillExists ? ws.activeGroupId : newGroups[0].id
		};
	});
	return true;
}

export function setActiveTab(tabId: string, groupId?: string): void {
	currentWorkspace.update((ws) => {
		if (!ws) return ws;
		const gid = groupId ?? ws.activeGroupId;
		return {
			...ws,
			activeGroupId: gid, // Clicking a tab in a group focuses that group
			groups: ws.groups.map((g) => {
				if (g.id !== gid) return g;
				if (g.activeTabId === tabId) return g;
				return {
					...g,
					activeTabId: tabId,
					tabHistory: pushTabHistory(g, g.activeTabId)
				};
			})
		};
	});
}

export function setActiveGroup(groupId: string): void {
	currentWorkspace.update((ws) =>
		ws && ws.activeGroupId !== groupId ? { ...ws, activeGroupId: groupId } : ws
	);
}

export function setHomeActiveGroup(groupId: string): void {
	homeState.update((state) =>
		state.activeGroupId === groupId ? state : { ...state, activeGroupId: groupId }
	);
}

export function setFileBrowserCwd(cwd: string): void {
	currentWorkspace.update((ws) => (ws ? { ...ws, fileBrowserCwd: cwd } : ws));
}

export function markTabUnsaved(tabId: string, unsaved: boolean): void {
	currentWorkspace.update((ws) => {
		if (!ws) return ws;
		return {
			...ws,
			groups: ws.groups.map((g) => ({
				...g,
				tabs: g.tabs.map((t) => (t.id === tabId ? { ...t, unsaved } : t))
			}))
		};
	});
}

export function updateTabFilePath(tabId: string, newPath: string): void {
	const name = getPathDisplayName(newPath, newPath);
	currentWorkspace.update((ws) => {
		if (!ws) return ws;
		return {
			...ws,
			groups: ws.groups.map((g) => ({
				...g,
				tabs: g.tabs.map((t) =>
					t.id === tabId ? { ...t, filePath: newPath, label: name, unsaved: false } : t
				)
			}))
		};
	});
}

export function clearTabEdit(tabId: string): void {
	currentWorkspace.update((ws) => {
		if (!ws) return ws;
		return {
			...ws,
			groups: ws.groups.map((g) => ({
				...g,
				tabs: g.tabs.map((t) => {
					if (t.id !== tabId || !t.edit) return t;
					const next = { ...t };
					delete next.edit;
					return next;
				})
			}))
		};
	});
}

// ── Split / Editor Group actions ────────────────────────────────

/** Open a file in a new editor group. */
export function openInSplit(filePath: string, direction?: SplitDirection): void {
	const ws = get(currentWorkspace);
	if (!ws) return;

	const dir = direction ?? ws.splitDirection ?? 'horizontal';

	// Create a new group with this file
	const name = getPathDisplayName(filePath, filePath);
	const newTab: Tab = { id: nextId(), type: 'file', label: name, filePath };
	const newGroup: EditorGroup = {
		id: nextId(),
		tabs: [newTab],
		activeTabId: newTab.id
	};

	currentWorkspace.update((ws) => {
		if (!ws) return ws;
		return {
			...ws,
			groups: [...ws.groups, newGroup],
			activeGroupId: newGroup.id,
			layout: splitLayout(ws.layout, ws.activeGroupId, newGroup.id, dir),
			splitDirection: dir,
			splitRatio: ws.splitRatio ?? 0.5
		};
	});
}

/** Split the current active tab into a new group */
export function splitCurrentTab(direction?: SplitDirection): void {
	const ws = get(currentWorkspace);
	if (!ws) return;
	const group = ws.groups.find((g) => g.id === ws.activeGroupId);
	if (!group) return;
	const tab = group.tabs.find((t) => t.id === group.activeTabId);
	if (!tab) return;

	const dir = direction ?? ws.splitDirection ?? 'horizontal';

	// Copy the tab into a new group
	const newTab: Tab = { ...tab, id: nextId(), permanent: false };
	const newGroup: EditorGroup = {
		id: nextId(),
		tabs: [newTab],
		activeTabId: newTab.id
	};

	currentWorkspace.update((ws) => {
		if (!ws) return ws;
		return {
			...ws,
			groups: [...ws.groups, newGroup],
			activeGroupId: newGroup.id,
			layout: splitLayout(ws.layout, group.id, newGroup.id, dir),
			splitDirection: dir,
			splitRatio: ws.splitRatio ?? 0.5
		};
	});
}

export function splitHomeTab(direction?: SplitDirection): void {
	homeState.update((state) => {
		const group = state.groups.find((item) => item.id === state.activeGroupId);
		const tab = group?.tabs.find((item) => item.id === group.activeTabId);
		if (!group || !tab) return state;
		const dir = direction ?? state.splitDirection;
		const newTab: Tab = { ...tab, id: nextId(), permanent: false };
		const newGroup: EditorGroup = { id: nextId(), tabs: [newTab], activeTabId: newTab.id };
		return {
			...state,
			groups: [...state.groups, newGroup],
			activeGroupId: newGroup.id,
			layout: splitLayout(state.layout, group.id, newGroup.id, dir),
			splitDirection: dir
		};
	});
}

/** Close an entire editor group */
export function closeGroup(groupId: string): void {
	currentWorkspace.update((ws) => {
		if (!ws) return ws;

		const closingGroup = ws.groups.find((g) => g.id === groupId);
		const remainingGroups = ws.groups.filter((g) => g.id !== groupId);
		const targetGroup =
			remainingGroups.find((g) => g.id === ws.activeGroupId) ?? remainingGroups[0];
		if (!closingGroup || !targetGroup) return ws;

		const existingTabIds = new Set(targetGroup.tabs.map((t) => t.id));
		const movedTabs = closingGroup.tabs.filter((t) => !existingTabIds.has(t.id));
		const tabs = [...targetGroup.tabs, ...movedTabs];
		const activeTabId =
			ws.activeGroupId === closingGroup.id && tabs.some((t) => t.id === closingGroup.activeTabId)
				? closingGroup.activeTabId
				: targetGroup.activeTabId;
		const newGroups = remainingGroups.map((g) =>
			g.id === targetGroup.id
				? {
						...g,
						tabs,
						activeTabId: tabs.some((t) => t.id === activeTabId) ? activeTabId : (tabs[0]?.id ?? '')
					}
				: g
		);

		return {
			...ws,
			groups: newGroups,
			activeGroupId: targetGroup.id,
			layout: removeLayoutGroup(ws.layout, groupId) ?? { type: 'group', groupId: targetGroup.id }
		};
	});
}

export function closeHomeGroup(groupId: string): void {
	homeState.update((state) => {
		if (state.groups.length < 2) return state;
		const closingGroup = state.groups.find((group) => group.id === groupId);
		const remainingGroups = state.groups.filter((group) => group.id !== groupId);
		const targetGroup =
			remainingGroups.find((group) => group.id === state.activeGroupId) ?? remainingGroups[0];
		if (!closingGroup || !targetGroup) return state;
		const existingTabIds = new Set(targetGroup.tabs.map((tab) => tab.id));
		const tabs = [
			...targetGroup.tabs,
			...closingGroup.tabs.filter((tab) => !existingTabIds.has(tab.id))
		];
		const activeTabId =
			state.activeGroupId === groupId && tabs.some((tab) => tab.id === closingGroup.activeTabId)
				? closingGroup.activeTabId
				: targetGroup.activeTabId;
		return {
			...state,
			groups: remainingGroups.map((group) =>
				group.id === targetGroup.id ? { ...group, tabs, activeTabId } : group
			),
			activeGroupId: targetGroup.id,
			layout: removeLayoutGroup(state.layout, groupId) ?? { type: 'group', groupId: targetGroup.id }
		};
	});
}

type SplitEditorState = Pick<
	WorkspaceState,
	'groups' | 'activeGroupId' | 'layout' | 'splitDirection'
>;

function moveTabToGroupInState<T extends SplitEditorState>(
	state: T,
	tabId: string,
	fromGroupId: string,
	toGroupId: string,
	allowPermanent: boolean
): T {
	const fromGroup = state.groups.find((g) => g.id === fromGroupId);
	if (!fromGroup || fromGroupId === toGroupId) return state;
	const tab = fromGroup.tabs.find((t) => t.id === tabId);
	if (!tab || (!allowPermanent && tab.permanent)) return state;

	let newGroups = state.groups.map((g) => {
		if (g.id === fromGroupId) {
			const newTabs = g.tabs.filter((t) => t.id !== tabId);
			const newActiveId = g.activeTabId === tabId ? (newTabs[0]?.id ?? 'files') : g.activeTabId;
			return { ...g, tabs: newTabs, activeTabId: newActiveId };
		}
		if (g.id === toGroupId) {
			return { ...g, tabs: [...g.tabs, tab], activeTabId: tab.id };
		}
		return g;
	});

	newGroups = newGroups.filter((g) => g.tabs.length > 0);

	const sourceGroupStillExists = newGroups.some((g) => g.id === fromGroupId);
	const targetGroupStillExists = newGroups.some((g) => g.id === toGroupId);
	return {
		...state,
		groups: newGroups,
		layout: sourceGroupStillExists
			? state.layout
			: (removeLayoutGroup(state.layout, fromGroupId) ?? state.layout),
		activeGroupId: targetGroupStillExists ? toGroupId : newGroups[0].id
	};
}

/** Move a tab from one group to another */
export function moveTabToGroup(tabId: string, fromGroupId: string, toGroupId: string): void {
	currentWorkspace.update((workspace) =>
		workspace ? moveTabToGroupInState(workspace, tabId, fromGroupId, toGroupId, true) : workspace
	);
}

export function moveHomeTabToGroup(tabId: string, fromGroupId: string, toGroupId: string): void {
	homeState.update((state) => moveTabToGroupInState(state, tabId, fromGroupId, toGroupId, true));
}

function moveTabToNewSplitInState<T extends SplitEditorState>(
	state: T,
	tabId: string,
	fromGroupId: string,
	targetGroupId: string,
	direction: SplitDirection,
	placement: 'before' | 'after' = 'after'
): T {
	const fromGroup = state.groups.find((g) => g.id === fromGroupId);
	if (!fromGroup) return state;
	const tab = fromGroup.tabs.find((t) => t.id === tabId);
	if (!tab || tab.permanent) return state;

	const newGroup: EditorGroup = {
		id: nextId(),
		tabs: [tab],
		activeTabId: tab.id
	};
	let groups = state.groups.map((g) => {
		if (g.id !== fromGroupId) return g;
		const tabs = g.tabs.filter((t) => t.id !== tabId);
		return { ...g, tabs, activeTabId: tabs[0]?.id ?? '' };
	});
	const sourceGroupRemoved = groups.some(
		(group) => group.id === fromGroupId && group.tabs.length === 0
	);
	groups = groups.filter((g) => g.tabs.length > 0);
	groups.push(newGroup);
	let layout = state.layout;
	if (sourceGroupRemoved) {
		layout =
			fromGroupId === targetGroupId
				? replaceLayoutGroup(layout, fromGroupId, newGroup.id)
				: (removeLayoutGroup(layout, fromGroupId) ?? layout);
	}
	if (!(sourceGroupRemoved && fromGroupId === targetGroupId)) {
		layout = splitLayout(layout, targetGroupId, newGroup.id, direction, placement);
	}

	return {
		...state,
		groups,
		activeGroupId: newGroup.id,
		layout,
		splitDirection: direction
	};
}

export function moveTabToNewSplit(
	tabId: string,
	fromGroupId: string,
	targetGroupId: string,
	direction: SplitDirection,
	placement: 'before' | 'after' = 'after'
): void {
	currentWorkspace.update((workspace) =>
		workspace
			? moveTabToNewSplitInState(workspace, tabId, fromGroupId, targetGroupId, direction, placement)
			: workspace
	);
}

export function moveHomeTabToNewSplit(
	tabId: string,
	fromGroupId: string,
	targetGroupId: string,
	direction: SplitDirection,
	placement: 'before' | 'after' = 'after'
): void {
	homeState.update((state) =>
		moveTabToNewSplitInState(state, tabId, fromGroupId, targetGroupId, direction, placement)
	);
}

export function setSplitDirection(direction: SplitDirection): void {
	currentWorkspace.update((ws) => (ws ? { ...ws, splitDirection: direction } : ws));
}

export function setSplitRatio(splitId: string, ratio: number): void {
	currentWorkspace.update((ws) =>
		ws
			? {
					...ws,
					layout: updateSplitRatio(ws.layout, splitId, ratio)
				}
			: ws
	);
}

export function setHomeSplitRatio(splitId: string, ratio: number): void {
	homeState.update((state) => ({
		...state,
		layout: updateSplitRatio(state.layout, splitId, ratio)
	}));
}

function updateSplitRatio(layout: EditorLayout, splitId: string, ratio: number): EditorLayout {
	if (layout.type === 'group') return layout;
	if (layout.id === splitId) return { ...layout, ratio: Math.max(0.2, Math.min(0.8, ratio)) };
	return {
		...layout,
		first: updateSplitRatio(layout.first, splitId, ratio),
		second: updateSplitRatio(layout.second, splitId, ratio)
	};
}
