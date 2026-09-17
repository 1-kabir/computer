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
import { streamingChatTabs, bridgeNotificationsMuted } from '$lib/stores/chat';
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
	type: 'home' | 'files' | 'terminal' | 'file' | 'chat' | 'preview' | 'browser'; // preview is migrated on load
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
	/**
	 * Cross-workspace tabs (MVP): workspace this tab's data belongs to.
	 * Absent = the currently loaded workspace (legacy tabs).
	 * Panes resolve data by this field, falling back to currentWorkspace.
	 *
	 * LIMITATIONS (documented, out of scope for MVP):
	 * - Unified sidebar tree: the sidebar still navigates one workspace at a time.
	 * - Live terminal/browser sessions are path-bound at creation; dragging a
	 *   live session tab across workspaces is not supported (open a new one via
	 *   the `open*InWorkspace` helpers instead). Only chat + file tabs are
	 *   restored across reloads via {@link CrossWorkspacePin}.
	 */
	workspacePath?: string;
}

const SUPPORTED_TAB_TYPES = new Set([
	'home',
	'files',
	'terminal',
	'file',
	'chat',
	'preview',
	'browser'
]);

function isSupportedTab(tab: { type?: unknown }): tab is Tab {
	return typeof tab.type === 'string' && SUPPORTED_TAB_TYPES.has(tab.type);
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

/**
 * A persisted reference to a cross-workspace tab (Feature 2 MVP).
 * The live tab itself persists inside its host workspace's server-side layout;
 * pins mirror the currently-open foreign tabs so future UI (e.g. a "pinned
 * across workspaces" tray or reopen-closed affordance) has a stable source.
 * Keyed by tab id; added when a foreign tab opens, removed when it closes.
 */
export interface CrossWorkspacePin {
	tabId: string;
	/** Session (loaded workspace path) that hosts the foreign tab. */
	hostPath: string;
	workspacePath: string;
	kind: 'chat' | 'file';
	ref: string; // chat id or absolute file path
	label: string;
}

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
	bridgeNotificationsMuted?: boolean;
	autoContinue?: boolean;
	homeGroup?: EditorGroup;
	homeState?: HomeState;
	crossWorkspacePins?: CrossWorkspacePin[];
	git?: {
		identity?: {
			name?: string;
			email?: string;
		};
	};
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
export type GitReviewTarget = { kind: 'local' } | { kind: 'pr'; number: number };
export const gitReviewOpen = writable<GitReviewTarget | null>(null);
export const isGitRepo = writable(false);
export type StreamingBehavior = 'queue' | 'interrupt';
export const streamingBehavior = writable<StreamingBehavior>('queue');
export const pwaPreferences = writable<PwaPreferences>(defaultPwaPreferences);
export const themeConfig = writable<ThemeConfig | null>(null);
export const textScale = writable<number | null>(null);
export const borderContrast = writable<number | null>(null);
export const widescreenMode = writable(false);
export const expandToolDetails = writable(false);
/** Auto-send a continue message when the harness detects a truncated provider response. */
export const autoContinue = writable(false);

/** Saved workspace path order for sidebar drag-reorder. */
export const workspaceOrder = writable<string[]>([]);

/**
 * Persisted refs to open cross-workspace (foreign) tabs.
 * The live tabs persist inside their host workspace's server-side layout;
 * pins mirror them here in UserPreferences so future UI (a pinned tray,
 * reopen-closed affordance) has a stable source. Keyed by tab id.
 */
export const crossWorkspacePins = writable<CrossWorkspacePin[]>([]);

function upsertCrossWorkspacePin(pin: CrossWorkspacePin): void {
	crossWorkspacePins.update((pins) => [...pins.filter((p) => p.tabId !== pin.tabId), pin]);
}

function removeCrossWorkspacePin(tabId: string): void {
	crossWorkspacePins.update((pins) =>
		pins.some((p) => p.tabId === tabId) ? pins.filter((p) => p.tabId !== tabId) : pins
	);
}

/**
 * Workspace a tab's data belongs to: the tab's own stamp, or the currently
 * loaded workspace for legacy tabs. Panes must resolve data by this, never
 * by assuming the tab belongs to the host workspace.
 */
export function resolveTabWorkspace(tab: Tab): string {
	return tab.workspacePath ?? get(currentWorkspace)?.path ?? '';
}

/** True when the tab shows data from a workspace other than the loaded one. */
export function isForeignTab(tab: Tab): boolean {
	const host = get(currentWorkspace)?.path;
	return !!tab.workspacePath && !!host && tab.workspacePath !== host;
}

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

function stripForeignTabs(ws: WorkspaceState): WorkspaceState {
	const groups = ws.groups
		.map((g) => {
			const tabs = g.tabs.filter((t) => !t.workspacePath || t.workspacePath === ws.path);
			const liveIds = new Set(tabs.map((t) => t.id));
			return {
				...g,
				tabs,
				tabHistory: (g.tabHistory ?? []).filter((id) => liveIds.has(id)),
				activeTabId: tabs.some((t) => t.id === g.activeTabId) ? g.activeTabId : (tabs[0]?.id ?? '')
			};
		})
		.filter((g) => g.tabs.length > 0);
	const kept = groups.length > 0 ? groups : [createDefaultGroup()];
	return {
		...ws,
		groups: kept,
		activeGroupId: kept.some((g) => g.id === ws.activeGroupId) ? ws.activeGroupId : kept[0].id,
		layout: normalizeLayout(
			ws.layout,
			kept,
			ws.splitDirection ?? 'horizontal',
			ws.splitRatio ?? 0.5
		)
	};
}

function persistWorkspace(): void {
	if (_saveWsTimer) clearTimeout(_saveWsTimer);
	_saveWsTimer = setTimeout(() => {
		const ws = get(currentWorkspace);
		if (!ws) return;
		// Foreign (cross-workspace) tabs are session-only: strip them so each
		// workspace's server-side layout keeps only its own tabs. Their refs
		// persist separately via crossWorkspacePins in UserPreferences.
		const stripped = stripForeignTabs(ws);
		saveWorkspaceState(stripped.path, stripped as unknown as Record<string, unknown>).catch(
			() => {}
		);
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
			bridgeNotificationsMuted: get(bridgeNotificationsMuted),
			autoContinue: get(autoContinue),
			homeState: get(homeState),
			crossWorkspacePins: get(crossWorkspacePins)
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
	crossWorkspacePins.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
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
	autoContinue.subscribe(() => {
		if (get(stateLoaded)) persistPreferences();
	});
	bridgeNotificationsMuted.subscribe(() => {
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
		if (prefs.autoContinue !== undefined) autoContinue.set(prefs.autoContinue as boolean);
		if (prefs.bridgeNotificationsMuted !== undefined)
			bridgeNotificationsMuted.set(prefs.bridgeNotificationsMuted as boolean);
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
			const aliveTerminals = new Set(terminalIds.map((session) => session.session_id));
			const aliveBrowsers = new Set(browserIds);
			let groups = savedHomeState.groups
				.map((group) => {
					const tabs = group.tabs.filter((tab): tab is Tab => isSupportedTab(tab));
					const liveTabs = tabs
						.filter(
							(tab) =>
								tab.type !== 'terminal' ||
								(tab.sessionId !== undefined && aliveTerminals.has(tab.sessionId))
						)
						.filter(
							(tab) =>
								tab.type !== 'browser' ||
								(tab.browserSessionId !== undefined && aliveBrowsers.has(tab.browserSessionId))
						);
					const liveIds = new Set(liveTabs.map((tab) => tab.id));
					return {
						...group,
						tabs: liveTabs,
						tabHistory: (group.tabHistory ?? []).filter((id) => liveIds.has(id)),
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
		const savedPins = prefs.crossWorkspacePins;
		if (Array.isArray(savedPins)) {
			crossWorkspacePins.set(
				savedPins.filter(
					(pin): pin is CrossWorkspacePin =>
						!!pin &&
						typeof pin.tabId === 'string' &&
						typeof pin.hostPath === 'string' &&
						typeof pin.workspacePath === 'string' &&
						(pin.kind === 'chat' || pin.kind === 'file') &&
						typeof pin.ref === 'string' &&
						typeof pin.label === 'string'
				)
			);
		}
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
								if (!isSupportedTab(tab)) return null;
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
					const liveIds = new Set(filteredTabs.map((t) => t.id));
					const activeStillExists = filteredTabs.some((t: Tab) => t.id === g.activeTabId);
					return {
						...g,
						tabs: filteredTabs,
						tabHistory: (g.tabHistory ?? []).filter((id) => liveIds.has(id)),
						activeTabId: activeStillExists ? g.activeTabId : (filteredTabs[0]?.id ?? 'files')
					};
				})
				.filter((g) => g.tabs.length > 0);

			const groups = cleanedGroups.length > 0 ? cleanedGroups : [createDefaultGroup()];
			const activeGroupId = groups.some((g) => g.id === ws.activeGroupId)
				? ws.activeGroupId
				: (groups[0]?.id ?? 'default');

			currentWorkspace.set(
				restoreCrossWorkspaceTabs({
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
				})
			);
		} else {
			// First time opening this workspace, create defaults
			currentWorkspace.set(
				restoreCrossWorkspaceTabs(createDefaultWorkspace(canonicalWorkspacePath))
			);
		}
	} catch {
		currentWorkspace.set(null);
	}
}

/**
 * Re-attach persisted cross-workspace tabs (chat/file only) hosted by this
 * session after a reload. Foreign pins whose host matches the loaded path
 * are re-created in the first group; pins with stale ids/refs were already
 * filtered at load. Terminal/browser sessions are path-bound and NOT
 * restored here (documented MVP limitation) — their old tabs stay closed.
 */
function restoreCrossWorkspaceTabs(state: WorkspaceState): WorkspaceState {
	const pins = get(crossWorkspacePins).filter(
		(pin) =>
			pin.hostPath === state.path &&
			(pin.kind === 'chat' || pin.kind === 'file') &&
			((pin.kind === 'chat' && !pin.ref.startsWith('new-') && !pin.ref.startsWith('pending-')) ||
				pin.kind === 'file')
	);
	if (!pins.length) return state;
	const target = state.groups[0];
	if (!target) return state;
	const existingRefs = new Set(
		target.tabs.map((t) =>
			t.type === 'chat' ? `chat:${t.path}` : t.type === 'file' ? `file:${t.filePath}` : ''
		)
	);
	const restored: Tab[] = [];
	for (const pin of pins) {
		const key = `${pin.kind}:${pin.ref}`;
		if (existingRefs.has(key)) continue;
		existingRefs.add(key);
		restored.push(
			pin.kind === 'chat'
				? {
						id: nextId(),
						type: 'chat',
						label: pin.label,
						path: pin.ref,
						workspacePath: pin.workspacePath
					}
				: {
						id: nextId(),
						type: 'file',
						label: pin.label,
						filePath: pin.ref,
						workspacePath: pin.workspacePath
					}
		);
	}
	if (!restored.length) return state;
	return {
		...state,
		groups: state.groups.map((g, i) => (i === 0 ? { ...g, tabs: [...g.tabs, ...restored] } : g))
	};
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

/** Update a chat tab's id/label after the backend assigns a real chat id. */
export function updateWorkspaceChatTab(
	tabId: string,
	chatId: string,
	label: string,
	groupId?: string
): void {
	const gid = groupId ?? get(currentWorkspace)?.activeGroupId;
	updateGroupTabs(gid, (tabs) => ({
		tabs: tabs.map((t) => (t.id === tabId ? { ...t, path: chatId, label } : t))
	}));
}

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

	// If chatId provided, reuse an existing LOCAL tab for it (never hijack a
	// foreign tab: same chat id in another workspace is different data).
	if (chatId) {
		const existing = group.tabs.find(
			(t) => t.type === 'chat' && t.path === chatId && !t.workspacePath
		);
		if (existing) {
			setActiveTab(existing.id, gid);
			return;
		}
	} else {
		// No chatId — reuse an existing new/pending LOCAL chat tab if one is open
		const existing = group.tabs.find(
			(t) =>
				t.type === 'chat' &&
				!t.workspacePath &&
				(t.path?.startsWith('new-') || t.path?.startsWith('pending-'))
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

/**
 * Open a chat from a DIFFERENT workspace side-by-side in the current view
 * (cross-workspace tab strip MVP). The tab is stamped with `workspacePath`
 * so its ChatPanel loads/sends against that workspace; the tab is pinned in
 * `crossWorkspacePins` and stripped from the host workspace's server-side
 * layout on save (session-only).
 *
 * Out of scope: live terminal/browser sessions are path-bound — only chat
 * (+ file via {@link openFileTabInWorkspace}) can be opened cross-workspace.
 */
export function openChatTabInWorkspace(
	chatId: string | undefined,
	workspacePath: string,
	targetGroupId?: string
): void {
	const ws = get(currentWorkspace);
	if (!ws) return;
	const hostPath = ws.path;
	if (!workspacePath || workspacePath === hostPath) {
		openChatTab(chatId, targetGroupId);
		return;
	}

	const gid = targetGroupId ?? ws.activeGroupId;
	const group = ws.groups.find((g) => g.id === gid);
	if (!group) return;

	if (chatId) {
		const existing = group.tabs.find(
			(t) => t.type === 'chat' && t.path === chatId && t.workspacePath === workspacePath
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
		path: chatId || `new-${Date.now()}`,
		workspacePath
	};
	upsertCrossWorkspacePin({
		tabId: newTab.id,
		hostPath,
		workspacePath,
		kind: 'chat',
		ref: newTab.path!,
		label: newTab.label
	});

	updateGroupTabs(gid, (tabs) => ({
		tabs: [...tabs, newTab],
		activeTabId: newTab.id
	}));
}

/**
 * Open a file from a DIFFERENT workspace side-by-side (MVP companion to
 * {@link openChatTabInWorkspace}). The FileEditor resolves git/workspace
 * context from the tab's `workspacePath` (see `workspaceRoot` prop).
 */
export function openFileTabInWorkspace(
	filePath: string,
	workspacePath: string,
	targetGroupId?: string
): void {
	const ws = get(currentWorkspace);
	if (!ws) return;
	if (!workspacePath || workspacePath === ws.path) {
		openFileTab(filePath, targetGroupId);
		return;
	}

	const gid = targetGroupId ?? ws.activeGroupId;
	const group = ws.groups.find((g) => g.id === gid);
	if (!group) return;

	const existing = group.tabs.find(
		(t) => t.type === 'file' && t.filePath === filePath && t.workspacePath === workspacePath
	);
	if (existing) {
		setActiveTab(existing.id, gid);
		return;
	}

	const newTab: Tab = {
		id: nextId(),
		type: 'file',
		label: getPathDisplayName(filePath, filePath),
		filePath,
		workspacePath
	};
	upsertCrossWorkspacePin({
		tabId: newTab.id,
		hostPath: ws.path,
		workspacePath,
		kind: 'file',
		ref: filePath,
		label: newTab.label
	});

	updateGroupTabs(gid, (tabs) => ({
		tabs: [...tabs, newTab],
		activeTabId: newTab.id
	}));
}

/** Keep a foreign tab's persisted pin in sync after renames/title loads. No-op when unchanged. */
export function syncCrossWorkspacePin(tabId: string, label: string, ref?: string): void {
	crossWorkspacePins.update((pins) => {
		const pin = pins.find((p) => p.tabId === tabId);
		if (!pin) return pins;
		const nextRef = ref ?? pin.ref;
		if (pin.label === label && pin.ref === nextRef) return pins;
		return pins.map((p) => (p.tabId === tabId ? { ...p, label, ref: nextRef } : p));
	});
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

	// Drop the persisted cross-workspace pin for foreign tabs
	if (tab.workspacePath) removeCrossWorkspacePin(tabId);

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
