import { fetchJSON, jsonBody } from '$lib/apis';

export type MemoryScope = 'user' | 'workspace';
export type MemoryOperation = {
	action: 'add' | 'replace' | 'remove' | 'link' | 'move' | 'split' | 'merge';
	content?: string;
	old_text?: string;
	path?: string;
	new_path?: string;
	source_path?: string;
	target_path?: string;
	heading?: string;
	memory_id?: string;
	link?: string;
};

export type MemorySettings = {
	enabled: boolean;
	tool_enabled: boolean;
	background_review_enabled: boolean;
	'background_review.model'?: string | null;
	review_interval_turns: number;
	user_char_limit: number;
	workspace_char_limit: number;
};

export type MemoryState = {
	settings: MemorySettings;
	user: { entries: string[]; usage: string; path: string; root: string };
	workspace: { entries: string[]; usage: string; path: string; root: string };
};

export type MemorySnippet = {
	scope: MemoryScope;
	path: string;
	heading: string;
	memory_id: string;
	snippet: string;
	links: string[];
	reason: string;
};

export type MemoryFile = {
	scope: MemoryScope;
	path: string;
	size: number;
	modified_at: number;
	headings: string[];
	baseline: boolean;
	trash: boolean;
};

export const getMemory = (workspace: string) =>
	fetchJSON<MemoryState>(`/api/memory?workspace=${encodeURIComponent(workspace || '')}`);

export const updateMemorySettings = (settings: Partial<MemorySettings>) =>
	fetchJSON<{ settings: MemorySettings }>('/api/memory/config', {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ settings })
	});

export const updateMemory = (
	scope: MemoryScope,
	workspace: string,
	operations: MemoryOperation[]
) => fetchJSON('/api/memory/update', jsonBody({ scope, workspace, operations }));

export const searchMemory = (body: {
	query?: string;
	scope?: MemoryScope | 'both';
	workspace?: string;
	path?: string | null;
	memory_id?: string | null;
	expand_links?: boolean;
	include_trash?: boolean;
	limit?: number;
}) => fetchJSON<{ results: MemorySnippet[]; count: number }>('/api/memory/search', jsonBody(body));

export const listMemoryFiles = (
	workspace: string,
	options: {
		scope?: MemoryScope | 'both';
		q?: string;
		limit?: number;
		include_trash?: boolean;
	} = {}
) => {
	const params = new URLSearchParams({
		workspace: workspace || '',
		scope: options.scope || 'both',
		q: options.q || '',
		limit: String(options.limit || 100),
		include_trash: String(Boolean(options.include_trash))
	});
	return fetchJSON<{ files: MemoryFile[]; count: number }>(`/api/memory/files?${params}`);
};

export const getMemoryFile = (workspace: string, scope: MemoryScope, path: string) => {
	const params = new URLSearchParams({ workspace: workspace || '', scope, path });
	return fetchJSON<{ scope: MemoryScope; path: string; content: string; sections: unknown[] }>(
		`/api/memory/file?${params}`
	);
};

export const reviewMemory = (workspace: string) =>
	fetchJSON<{ changed: unknown[]; skipped: unknown[] }>(
		'/api/memory/review',
		jsonBody({ workspace })
	);
