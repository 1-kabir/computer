/**
 * Configurable keyboard shortcuts system.
 *
 * Every shortcut is user-configurable and persisted in preferences.
 * Ships with conventional IDE defaults that avoid conflicting with
 * browser-native shortcuts (Cmd+W, Cmd+T, Cmd+N, Cmd+1-9, Ctrl+Tab).
 *
 * Chord format: modifiers in fixed order + key, e.g. "Cmd+Shift+K"
 * Modifier order: Cmd → Ctrl → Alt → Shift
 * "Cmd" normalizes Meta (Mac) and Ctrl (Win/Linux) into one token.
 */

import { writable, get } from 'svelte/store';
import {
	openUntitledFileTab,
	openTerminalTab,
	openBrowserTab,
	closeTab,
	setActiveTab,
	activeGroup,
	currentWorkspace,
	splitActive,
	splitCurrentTab,
	closeGroup,
	sidebarOpen
} from '$lib/stores';
import { openChatTab } from '$lib/stores';

// ── Action IDs ──────────────────────────────────────────────────

export const ACTION_IDS = [
	'newFile',
	'newTerminal',
	'newChat',
	'newBrowser',
	'closeTab',
	'nextTab',
	'prevTab',
	'quickOpen',
	'searchAll',
	'openSettings',
	'toggleSplit',
	'toggleSidebar',
	'approveToolCall',
	'rejectToolCall',
	'voiceMemo'
] as const;

export type ActionId = (typeof ACTION_IDS)[number];

// ── Human-readable labels ───────────────────────────────────────

export const ACTION_LABELS: Record<ActionId, string> = {
	newFile: 'New File',
	newTerminal: 'New Terminal',
	newChat: 'New Chat',
	newBrowser: 'New Browser',
	closeTab: 'Close Tab',
	nextTab: 'Next Tab',
	prevTab: 'Previous Tab',
	quickOpen: 'Quick Open',
	searchAll: 'Search',
	openSettings: 'Open Settings',
	toggleSplit: 'Toggle Split',
	toggleSidebar: 'Toggle Sidebar',
	approveToolCall: 'Approve Tool Call',
	rejectToolCall: 'Reject Tool Call',
	voiceMemo: 'Voice Memo'
};

// ── Default bindings (browser-safe) ─────────────────────────────

export const DEFAULT_KEYBINDINGS: Record<ActionId, string> = {
	newFile: 'Cmd+N',
	newTerminal: 'Ctrl+`',
	newChat: 'Cmd+Shift+O',
	newBrowser: 'Cmd+Shift+B',
	closeTab: 'Cmd+W',
	nextTab: 'Cmd+Shift+]',
	prevTab: 'Cmd+Shift+[',
	quickOpen: 'Cmd+K',
	searchAll: 'Cmd+Shift+F',
	openSettings: 'Cmd+.',
	toggleSplit: 'Cmd+\\',
	toggleSidebar: 'Cmd+Shift+S',
	approveToolCall: 'Cmd+Shift+Enter',
	rejectToolCall: 'Cmd+Shift+Backspace',
	voiceMemo: 'Cmd+Shift+M'
};

// ── Store ───────────────────────────────────────────────────────

export type KeybindingsMap = Record<ActionId, string>;

export const keybindings = writable<KeybindingsMap>({ ...DEFAULT_KEYBINDINGS });

/** Load keybindings from saved preferences (partial map is OK). */
export function loadKeybindings(saved: Partial<Record<string, string>> | undefined): void {
	if (!saved || typeof saved !== 'object') return;
	keybindings.update((current) => {
		const updated = { ...current };
		for (const id of ACTION_IDS) {
			if (typeof saved[id] === 'string' && saved[id]) {
				updated[id] = saved[id]!;
			}
		}
		return updated;
	});
}

/** Reset all keybindings to defaults. */
export function resetKeybindings(): void {
	keybindings.set({ ...DEFAULT_KEYBINDINGS });
}

// ── Chord normalisation ─────────────────────────────────────────

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent);

/**
 * Convert a KeyboardEvent into a normalised chord string.
 *
 * On Mac: metaKey → "Cmd", ctrlKey stays as "Ctrl" (for Ctrl+` etc.)
 * On Win/Linux: ctrlKey → "Cmd" (so "Cmd+K" means Ctrl+K there)
 *
 * Modifier order: Cmd → Ctrl → Alt → Shift → Key
 */
export function eventToChord(e: KeyboardEvent): string {
	const parts: string[] = [];

	if (IS_MAC) {
		if (e.metaKey) parts.push('Cmd');
		if (e.ctrlKey) parts.push('Ctrl');
	} else {
		// Windows/Linux: Ctrl acts as Cmd
		if (e.ctrlKey) parts.push('Cmd');
	}

	if (e.altKey) parts.push('Alt');
	if (e.shiftKey) parts.push('Shift');

	// Normalise the key
	let key = e.key;

	// Skip pure modifier presses
	if (['Meta', 'Control', 'Alt', 'Shift'].includes(key)) return '';

	// Normalise common key names
	if (key === ' ') key = 'Space';
	if (key === 'Escape') key = 'Escape';
	if (key.length === 1) key = key.toUpperCase();

	// Bracket keys come through as the character when Shift is held
	// but we want the base key name
	if (key === '{') key = '[';
	if (key === '}') key = ']';

	parts.push(key);
	return parts.join('+');
}

/**
 * Format a chord string for display (e.g. "Cmd+Shift+N" → "⌘⇧N" on Mac).
 */
export function formatChord(chord: string): string {
	if (!chord) return '';
	const parts = chord.split('+');
	const formatted = parts.map((part) => {
		if (IS_MAC) {
			switch (part) {
				case 'Cmd':
					return '⌘';
				case 'Ctrl':
					return '⌃';
				case 'Alt':
					return '⌥';
				case 'Shift':
					return '⇧';
				default:
					return part;
			}
		}
		return part;
	});
	// On Mac, join directly like Open WebUI; on Win/Linux use +
	return IS_MAC ? formatted.join('') : formatted.join('+');
}

/**
 * Split a chord into individual display tokens for rendering as separate pills.
 * e.g. "Cmd+Shift+N" → ["⌘", "⇧", "N"] on Mac, ["Ctrl", "Shift", "N"] on Win.
 */
export function chordToDisplayParts(chord: string): string[] {
	if (!chord) return [];
	const parts = chord.split('+');
	return parts.map((part) => {
		if (IS_MAC) {
			switch (part) {
				case 'Cmd':
					return '⌘';
				case 'Ctrl':
					return '⌃';
				case 'Alt':
					return '⌥';
				case 'Shift':
					return '⇧';
				default:
					return part;
			}
		}
		return part;
	});
}

// ── Event matching ──────────────────────────────────────────────

/** Build a reverse lookup from chord → action. */
function buildReverseLookup(bindings: KeybindingsMap): Map<string, ActionId> {
	const map = new Map<string, ActionId>();
	for (const id of ACTION_IDS) {
		const chord = bindings[id];
		if (chord) map.set(chord, id);
	}
	return map;
}

/**
 * Match a KeyboardEvent against current keybindings.
 * Returns the matching ActionId or null.
 */
export function matchKeybinding(e: KeyboardEvent): ActionId | null {
	const chord = eventToChord(e);
	if (!chord) return null;
	const lookup = buildReverseLookup(get(keybindings));
	return lookup.get(chord) ?? null;
}

// ── Action executor ─────────────────────────────────────────────

/**
 * Execute a keybinding action.
 * Returns true if an action was executed, false if not
 * (e.g. quickOpen is handled externally via callback).
 */
export function executeAction(
	action: ActionId,
	callbacks?: {
		toggleQuickOpen?: () => void;
		toggleSettings?: () => void;
		toggleSearch?: () => void;
		toggleVoiceMemo?: () => void;
		approveToolCall?: () => boolean;
		rejectToolCall?: () => boolean;
	}
): boolean {
	const dispatchHomeAction = (
		detail:
			| 'newChat'
			| 'newTerminal'
			| 'newBrowser'
			| 'closeTab'
			| 'nextTab'
			| 'prevTab'
			| 'toggleSplit'
	) => {
		if (typeof window !== 'undefined')
			window.dispatchEvent(new CustomEvent('cptr:home-action', { detail }));
	};

	switch (action) {
		case 'newFile':
			openUntitledFileTab();
			return true;

		case 'newTerminal':
			if (get(currentWorkspace)) openTerminalTab();
			else dispatchHomeAction('newTerminal');
			return true;

		case 'newChat':
			if (get(currentWorkspace)) openChatTab();
			else dispatchHomeAction('newChat');
			return true;

		case 'newBrowser':
			if (get(currentWorkspace)) void openBrowserTab();
			else dispatchHomeAction('newBrowser');
			return true;

		case 'closeTab': {
			if (!get(currentWorkspace)) {
				dispatchHomeAction('closeTab');
				return true;
			}
			const group = get(activeGroup);
			if (!group) return false;
			const tab = group.tabs.find((t) => t.id === group.activeTabId);
			if (tab && !tab.permanent) {
				closeTab(tab.id, group.id);
			}
			return true;
		}

		case 'nextTab':
		case 'prevTab': {
			if (!get(currentWorkspace)) {
				dispatchHomeAction(action);
				return true;
			}
			const g = get(activeGroup);
			if (!g || g.tabs.length < 2) return true;
			const currentIdx = g.tabs.findIndex((t) => t.id === g.activeTabId);
			if (currentIdx === -1) return true;
			const dir = action === 'nextTab' ? 1 : -1;
			const nextIdx = (currentIdx + dir + g.tabs.length) % g.tabs.length;
			setActiveTab(g.tabs[nextIdx].id, g.id);
			return true;
		}

		case 'quickOpen':
			callbacks?.toggleQuickOpen?.();
			return true;

		case 'openSettings':
			callbacks?.toggleSettings?.();
			return true;

		case 'searchAll':
			callbacks?.toggleSearch?.();
			return true;

		case 'toggleSplit': {
			const ws = get(currentWorkspace);
			if (!ws) {
				dispatchHomeAction('toggleSplit');
				return true;
			}
			if (get(splitActive)) {
				const otherGroup = ws.groups.find((g) => g.id !== ws.activeGroupId);
				if (otherGroup) closeGroup(otherGroup.id);
			} else {
				splitCurrentTab();
			}
			return true;
		}

		case 'toggleSidebar':
			sidebarOpen.update((v) => !v);
			return true;

		case 'approveToolCall': {
			return callbacks?.approveToolCall?.() ?? false;
		}

		case 'rejectToolCall': {
			return callbacks?.rejectToolCall?.() ?? false;
		}

		case 'voiceMemo':
			callbacks?.toggleVoiceMemo?.();
			return true;

		default:
			return false;
	}
}
