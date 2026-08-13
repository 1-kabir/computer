/**
 * Git API: status, log, diff, stage, commit, push, pull, branches.
 */
import { fetchJSON, jsonBody } from '$lib/apis';

export interface GitIdentity {
	name?: string;
	email?: string;
	name_source?: string;
	email_source?: string;
}

export interface GhAccount {
	state?: string;
	active?: boolean;
	host?: string;
	login?: string;
	tokenSource?: string;
	scopes?: string;
	gitProtocol?: string;
}

export interface GitSettingsConfig {
	root: string;
	git: {
		installed: boolean;
		version?: string | null;
		is_repo: boolean;
		identity?: GitIdentity;
		credential_helpers?: string[];
		remote_url?: string;
	};
	app_identity?: GitIdentity;
	gh: {
		installed: boolean;
		version?: string | null;
		hosts?: Record<string, GhAccount[]>;
		message?: string;
	};
	permissions: {
		can_manage_gh: boolean;
		can_manage_commit_model: boolean;
	};
}

export interface GhLoginStatus {
	session_id: string;
	status: 'pending' | 'complete' | 'failed' | 'expired';
	verification_uri?: string;
	user_code?: string;
	auth?: GitSettingsConfig['gh'];
}

export interface GitOperationResult {
	ok: boolean;
	message: string;
}

export interface GitPrAuthor {
	login?: string;
	name?: string;
}

export interface GitPr {
	number: number;
	title: string;
	url: string;
	state?: string;
	isDraft?: boolean;
	reviewDecision?: string;
	statusCheckRollup?: unknown[];
	headRefName?: string;
	baseRefName?: string;
	author?: GitPrAuthor;
	assignees?: GitPrAuthor[];
	labels?: { name?: string; color?: string; description?: string }[];
	milestone?: { title?: string } | null;
	reviewRequests?: unknown[];
	latestReviews?: unknown[];
	mergeable?: string;
	mergeStateStatus?: string;
	maintainerCanModify?: boolean;
	createdAt?: string;
	updatedAt?: string;
	additions?: number;
	deletions?: number;
	changedFiles?: number;
	body?: string;
	comments?: unknown[];
	reviews?: unknown[];
	commits?: unknown[];
	files?: unknown[];
}

export interface GitPrCapabilities {
	is_github: boolean;
	gh_installed: boolean;
	authenticated: boolean;
	default_branch?: string;
	message?: string;
}

export interface GitPrCheck {
	name?: string;
	workflow?: string;
	state?: string;
	bucket?: 'pass' | 'fail' | 'pending' | 'skipping' | 'cancel' | string;
	description?: string;
	link?: string;
	startedAt?: string;
	completedAt?: string;
}

// Deduplicate concurrent getGitStatus calls.
// Multiple components (layout, GitBar, FileEditor × N) all fetch on mount;
// this ensures they share a single in-flight request and a brief result cache.
const _statusCache = new Map<string, { promise: Promise<unknown>; ts: number }>();
const STATUS_CACHE_MS = 2000;

export const getGitStatus = (root: string): Promise<unknown> => {
	const cached = _statusCache.get(root);
	if (cached && Date.now() - cached.ts < STATUS_CACHE_MS) {
		return cached.promise;
	}
	const promise = fetchJSON(`/api/git/status?root=${encodeURIComponent(root)}`);
	_statusCache.set(root, { promise, ts: Date.now() });
	// Clean up after cache window
	promise.finally(() => {
		setTimeout(() => {
			const entry = _statusCache.get(root);
			if (entry && entry.promise === promise) {
				_statusCache.delete(root);
			}
		}, STATUS_CACHE_MS);
	});
	return promise;
};

/** Force a fresh git status fetch, bypassing the cache. */
export const getGitStatusFresh = (root: string): Promise<unknown> => {
	_statusCache.delete(root);
	return getGitStatus(root);
};

export const getGitLog = (root: string, limit = 30) =>
	fetchJSON(`/api/git/log?root=${encodeURIComponent(root)}&limit=${limit}`);

export const getGitDiff = (params: string) => fetchJSON(`/api/git/diff?${params}`);

export const getGitShow = (root: string, ref: string, ignoreWhitespace = false) =>
	fetchJSON(
		`/api/git/show?root=${encodeURIComponent(root)}&ref=${encodeURIComponent(ref)}&ignore_whitespace=${ignoreWhitespace}`
	);

export const getGitBranches = (root: string) =>
	fetchJSON(`/api/git/branches?root=${encodeURIComponent(root)}`);

export const getGitWorktrees = (root: string) =>
	fetchJSON(`/api/git/worktrees?root=${encodeURIComponent(root)}`);

export const createGitWorktree = (root: string, branch: string, path?: string) =>
	fetchJSON('/api/git/worktrees', jsonBody({ root, branch, path }));

export const getGitStashes = (root: string) =>
	fetchJSON(`/api/git/stashes?root=${encodeURIComponent(root)}`);

export const getGitConfig = (root?: string) =>
	fetchJSON<GitSettingsConfig>(`/api/git/config${root ? `?root=${encodeURIComponent(root)}` : ''}`);

export const startGhLogin = (hostname = 'github.com', git_protocol = 'https') =>
	fetchJSON<GhLoginStatus>('/api/git/gh/login/start', jsonBody({ hostname, git_protocol }));

export const getGhLoginStatus = (session_id: string) =>
	fetchJSON<GhLoginStatus>('/api/git/gh/login/status', jsonBody({ session_id }));

export const cancelGhLogin = (session_id: string) =>
	fetchJSON('/api/git/gh/login/cancel', jsonBody({ session_id }));

export const ghLogout = (hostname = 'github.com', user?: string) =>
	fetchJSON('/api/git/gh/logout', jsonBody({ hostname, user }));

export const ghSwitch = (hostname: string, user: string) =>
	fetchJSON('/api/git/gh/switch', jsonBody({ hostname, user }));

export const ghSetupGit = (hostname = 'github.com') =>
	fetchJSON('/api/git/gh/setup-git', jsonBody({ hostname }));

export const getGitPrCapabilities = (root: string) =>
	fetchJSON<GitPrCapabilities>(`/api/git/pr/capabilities?root=${encodeURIComponent(root)}`);

export const getGitCurrentPr = (root: string) =>
	fetchJSON<{ found: boolean; pr?: GitPr | null }>(
		`/api/git/pr/current?root=${encodeURIComponent(root)}`
	);

export const getGitPrList = (
	root: string,
	state = 'open',
	scope = 'all',
	search = '',
	limit = 30
) =>
	fetchJSON<{ items: GitPr[] }>(
		`/api/git/pr/list?root=${encodeURIComponent(root)}&state=${encodeURIComponent(state)}&scope=${encodeURIComponent(scope)}&search=${encodeURIComponent(search)}&limit=${limit}`
	);

export const getGitPrView = (root: string, number: number) =>
	fetchJSON<GitPr>(
		`/api/git/pr/view?root=${encodeURIComponent(root)}&number=${encodeURIComponent(number)}`
	);

export const getGitPrDiff = (root: string, number: number, ignoreWhitespace = false) =>
	fetchJSON(
		`/api/git/pr/diff?root=${encodeURIComponent(root)}&number=${encodeURIComponent(number)}&ignore_whitespace=${ignoreWhitespace}`
	);

export const getGitCompareDiff = (
	root: string,
	base: string,
	head: string,
	ignoreWhitespace = false
) =>
	fetchJSON(
		`/api/git/compare/diff?root=${encodeURIComponent(root)}&base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}&ignore_whitespace=${ignoreWhitespace}`
	);

export const getGitPrChecks = (root: string, number: number) =>
	fetchJSON<{ items: GitPrCheck[] }>(
		`/api/git/pr/checks?root=${encodeURIComponent(root)}&number=${encodeURIComponent(number)}`
	);

export const gitPrCheckout = (root: string, number: number) =>
	fetchJSON<GitOperationResult>('/api/git/pr/checkout', jsonBody({ root, number }));

export const gitPrCreate = (
	root: string,
	body: {
		title: string;
		body?: string;
		repo?: string;
		base?: string;
		head?: string;
		draft?: boolean;
		reviewers?: string[];
		assignees?: string[];
		labels?: string[];
		milestone?: string;
		project?: string;
		maintainer_edit?: boolean;
	}
) => fetchJSON<{ ok: boolean; url?: string }>('/api/git/pr/create', jsonBody({ root, ...body }));

export const gitPrEdit = (
	root: string,
	number: number,
	body: {
		title?: string;
		body?: string;
		base?: string;
		add_reviewers?: string[];
		remove_reviewers?: string[];
		add_assignees?: string[];
		remove_assignees?: string[];
		add_labels?: string[];
		remove_labels?: string[];
		milestone?: string;
		remove_milestone?: boolean;
	}
) => fetchJSON<GitOperationResult>('/api/git/pr/edit', jsonBody({ root, number, ...body }));

export const gitPrReady = (root: string, number: number, draft = false) =>
	fetchJSON<GitOperationResult>('/api/git/pr/ready', jsonBody({ root, number, draft }));

export const gitPrClose = (
	root: string,
	number: number,
	{ comment = '', delete_branch = false }: { comment?: string; delete_branch?: boolean } = {}
) => fetchJSON<GitOperationResult>('/api/git/pr/close', jsonBody({ root, number, comment, delete_branch }));

export const gitPrReopen = (root: string, number: number) =>
	fetchJSON<GitOperationResult>('/api/git/pr/reopen', jsonBody({ root, number }));

export const gitPrUpdateBranch = (root: string, number: number, rebase = false) =>
	fetchJSON<GitOperationResult>('/api/git/pr/update-branch', jsonBody({ root, number, rebase }));

export const gitPrMerge = (
	root: string,
	number: number,
	body: {
		strategy?: 'merge' | 'squash' | 'rebase';
		auto?: boolean;
		delete_branch?: boolean;
		subject?: string;
		body?: string;
	} = {}
) => fetchJSON<GitOperationResult>('/api/git/pr/merge', jsonBody({ root, number, ...body }));

export const gitPrComment = (root: string, number: number, body: string) =>
	fetchJSON<GitOperationResult>('/api/git/pr/comment', jsonBody({ root, number, body }));

export const gitPrReview = (
	root: string,
	number: number,
	event: 'approve' | 'comment' | 'request_changes',
	body = ''
) => fetchJSON<GitOperationResult>('/api/git/pr/review', jsonBody({ root, number, event, body }));

export const stageFiles = (root: string, files: string[]) =>
	fetchJSON('/api/git/stage', jsonBody({ root, files }));

export const unstageFiles = (root: string, files: string[]) =>
	fetchJSON('/api/git/unstage', jsonBody({ root, files }));

export const discardChanges = (root: string, files: string[]) =>
	fetchJSON('/api/git/discard', jsonBody({ root, files }));

export const gitCommit = (root: string, message: string) =>
	fetchJSON('/api/git/commit', jsonBody({ root, message }));

export const generateGitCommitMessage = (root: string, modelId?: string) =>
	fetchJSON<{ summary: string; description: string }>(
		'/api/git/message',
		jsonBody({ root, model_id: modelId || undefined })
	);

export const gitPull = (root: string) =>
	fetchJSON<GitOperationResult>('/api/git/pull', jsonBody({ root }));

export const gitFetch = (root: string) =>
	fetchJSON<GitOperationResult>('/api/git/fetch', jsonBody({ root }));

export const gitPush = (
	root: string,
	{
		force = false,
		set_upstream = false,
		branch
	}: { force?: boolean; set_upstream?: boolean; branch?: string } = {}
) =>
	fetchJSON<GitOperationResult>('/api/git/push', jsonBody({ root, force, set_upstream, branch }));

export const gitUncommit = (root: string) => fetchJSON('/api/git/uncommit', jsonBody({ root }));

export const gitStash = (root: string, message?: string) =>
	fetchJSON('/api/git/stash', jsonBody({ root, message }));

export const gitUnstash = (root: string, index = 0) =>
	fetchJSON('/api/git/unstash', jsonBody({ root, index }));

export const createGitBranch = (root: string, name: string) =>
	fetchJSON('/api/git/branch', jsonBody({ root, name }));

export const renameGitBranch = (root: string, old_name: string, new_name: string) =>
	fetchJSON('/api/git/branch', {
		method: 'PATCH',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ root, old_name, new_name })
	});

export const deleteGitBranch = (root: string, name: string) =>
	fetchJSON('/api/git/branch', {
		method: 'DELETE',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ root, name })
	});

export const checkoutBranch = (root: string, branch: string) =>
	fetchJSON('/api/git/checkout', jsonBody({ root, branch }));

export const stageAll = (root: string) =>
	fetchJSON('/api/git/stage', jsonBody({ root, files: ['.'] }));

export const unstageAll = (root: string) =>
	fetchJSON('/api/git/unstage', jsonBody({ root, files: ['.'] }));
