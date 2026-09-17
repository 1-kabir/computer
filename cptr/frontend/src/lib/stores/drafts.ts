/**
 * Per-chat input draft persistence.
 *
 * Saves the in-progress chat input to `localStorage` (debounced by the
 * caller) so composed text survives panel/tab resizes, remounts, and page
 * reloads. Drafts are segmented by workspace + chat and never touch the
 * server — localStorage only.
 *
 * Key shape: `cptr:draft:<workspace>::<chatId|tabId>`. New/pending chats
 * have no real id yet, so callers key by `tabId` and migrate to the real
 * `chatId` once assigned (see `migrateDraft`).
 */

const DRAFT_PREFIX = 'cptr:draft:';

/** Drafts older than this are treated as stale and ignored (24h). */
export const DRAFT_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export interface ChatDraft {
	text: string;
	savedAt: number;
}

export function draftKey(workspace: string, chatIdOrTabId: string): string {
	return `${DRAFT_PREFIX}${workspace}::${chatIdOrTabId}`;
}

function storage(): Storage | null {
	try {
		return typeof localStorage !== 'undefined' ? localStorage : null;
	} catch {
		return null;
	}
}

/** Save (or clear, when empty) the draft for a chat scope. Best-effort. */
export function saveDraft(workspace: string, chatIdOrTabId: string, text: string): void {
	const store = storage();
	if (!store || !chatIdOrTabId) return;
	try {
		const key = draftKey(workspace, chatIdOrTabId);
		if (!text.trim()) {
			store.removeItem(key);
			return;
		}
		const draft: ChatDraft = { text, savedAt: Date.now() };
		store.setItem(key, JSON.stringify(draft));
	} catch {
		// Quota / private-mode: drafts are best-effort, never throw.
	}
}

/** Load a fresh draft, or null when missing/stale/invalid. Stale keys are removed. */
export function loadDraft(workspace: string, chatIdOrTabId: string): ChatDraft | null {
	const store = storage();
	if (!store || !chatIdOrTabId) return null;
	try {
		const key = draftKey(workspace, chatIdOrTabId);
		const raw = store.getItem(key);
		if (!raw) return null;
		const parsed = JSON.parse(raw) as Partial<ChatDraft>;
		if (typeof parsed.text !== 'string' || !parsed.text.trim()) {
			store.removeItem(key);
			return null;
		}
		if (typeof parsed.savedAt !== 'number' || Date.now() - parsed.savedAt > DRAFT_MAX_AGE_MS) {
			store.removeItem(key);
			return null;
		}
		return { text: parsed.text, savedAt: parsed.savedAt };
	} catch {
		return null;
	}
}

/** Drop the draft for a chat scope. */
export function clearDraft(workspace: string, chatIdOrTabId: string): void {
	const store = storage();
	if (!store || !chatIdOrTabId) return;
	try {
		store.removeItem(draftKey(workspace, chatIdOrTabId));
	} catch {
		// Best-effort.
	}
}

/**
 * Move a draft from a provisional scope (tabId for new/pending chats) to the
 * real chat id. The destination wins when both hold a draft.
 */
export function migrateDraft(workspace: string, fromId: string, toId: string): void {
	if (!fromId || !toId || fromId === toId) return;
	const store = storage();
	if (!store) return;
	try {
		const fromKey = draftKey(workspace, fromId);
		const raw = store.getItem(fromKey);
		if (!raw) return;
		const toKey = draftKey(workspace, toId);
		if (!store.getItem(toKey)) store.setItem(toKey, raw);
		store.removeItem(fromKey);
	} catch {
		// Best-effort.
	}
}
