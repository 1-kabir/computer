<script lang="ts">
	import { activeWorkspace, openChatTab, openFileTab, gitReviewOpen } from '$lib/stores';
	import {
		getGitDiff,
		getGitPrDiff,
		getGitPrView,
		gitPrComment,
		gitPrReview,
		type GitPr
	} from '$lib/apis/git';
	import { diffDisplayMode, hideWhitespaceChanges } from '$lib/stores/gitDiffSettings';
	import { gitStatusStore, type GitStatus, type GitFile } from '$lib/stores/gitStatus.svelte';

	import { tooltip } from '$lib/tooltip';
	import { t } from '$lib/i18n';
	import { countDiffStats, type DiffFile } from '$lib/utils/diff';
	import Icon from './Icon.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import DiffSettingsMenu from './DiffSettingsMenu.svelte';
	import DiffHunkList from './DiffHunkList.svelte';

	type ReviewFile = {
		key: string;
		path: string;
		status: string;
		staged: boolean;
		binary: boolean;
		diffFiles: DiffFile[];
		additions: number;
		deletions: number;
		expanded: boolean;
	};

	let gitStatus = $derived(gitStatusStore.status);
	let reviewFiles = $state<ReviewFile[]>([]);
	let loading = $state(false);
	let refreshing = $state(false);
	let error = $state('');
	let showDiffSettings = $state(false);
	let diffSettingsButtonEl = $state<HTMLButtonElement | undefined>();
	let pr = $state<GitPr | null>(null);
	let loadSeq = 0;

	const workspacePath = $derived($activeWorkspace?.path ?? '');
	const totalChanges = $derived(reviewFiles.length);
	const totalAdditions = $derived(reviewFiles.reduce((sum, file) => sum + file.additions, 0));
	const totalDeletions = $derived(reviewFiles.reduce((sum, file) => sum + file.deletions, 0));
	const anyExpanded = $derived(reviewFiles.some((file) => file.expanded));

	const reviewTarget = $derived($gitReviewOpen);
	const isPrReview = $derived(reviewTarget?.kind === 'pr');

	// React to git status/target changes from centralized store
	let _prevStatusRef: GitStatus | null = null;
	let _prevHideWhitespace = false;
	let _prevTargetKey = '';
	$effect(() => {
		const status = gitStatus;
		const hideWhitespace = $hideWhitespaceChanges;
		const target = reviewTarget;
		const targetKey = target ? `${target.kind}:${target.kind === 'pr' ? target.number : ''}` : '';
		if (!workspacePath || !target) {
			reviewFiles = [];
			return;
		}
		if (target.kind === 'pr') {
			if (targetKey !== _prevTargetKey || hideWhitespace !== _prevHideWhitespace) {
				const isInitial = _prevTargetKey !== targetKey;
				_prevTargetKey = targetKey;
				_prevHideWhitespace = hideWhitespace;
				fetchPrDiffs(target.number, isInitial);
			}
			return;
		}
		if (!status) {
			reviewFiles = [];
			return;
		}
		if (
			status !== _prevStatusRef ||
			hideWhitespace !== _prevHideWhitespace ||
			targetKey !== _prevTargetKey
		) {
			const isInitial = _prevStatusRef === null;
			_prevStatusRef = status;
			_prevHideWhitespace = hideWhitespace;
			_prevTargetKey = targetKey;
			pr = null;
			fetchDiffs(status, isInitial);
		}
	});

	async function fetchDiffs(status: GitStatus, initial = false) {
		const seq = ++loadSeq;
		const root = workspacePath;
		if (!root) return;

		if (initial) loading = true;
		else refreshing = true;
		error = '';

		try {
			if (!status.is_repo) {
				reviewFiles = [];
				return;
			}

			const previousExpansion = new Map(reviewFiles.map((file) => [file.key, file.expanded]));
			const nextFiles = await Promise.all(
				(status.files ?? []).map(async (file) => {
					const params = new URLSearchParams({
						root,
						file: file.path,
						staged: String(file.staged),
						ignore_whitespace: String($hideWhitespaceChanges)
					});
					if (file.status === 'untracked') params.set('untracked', 'true');

					let diffFiles: DiffFile[] = [];
					try {
						const diff = (await getGitDiff(params.toString())) as { files?: DiffFile[] };
						diffFiles = diff.files ?? [];
					} catch {
						diffFiles = [];
					}

					const counts = countDiffStats(diffFiles);
					const key = fileKey(file);
					return {
						key,
						path: file.path,
						status: file.status,
						staged: file.staged,
						binary: file.binary ?? false,
						diffFiles,
						additions: counts.additions,
						deletions: counts.deletions,
						expanded: previousExpansion.get(key) ?? true
					};
				})
			);

			if (seq !== loadSeq || root !== workspacePath) return;
			reviewFiles = nextFiles;
		} catch (e) {
			if (seq !== loadSeq) return;
			error = e instanceof Error ? e.message : $t('git.loadChangesFailed');
			reviewFiles = [];
		} finally {
			if (seq === loadSeq) {
				loading = false;
				refreshing = false;
			}
		}
	}

	async function fetchPrDiffs(number: number, initial = false) {
		const seq = ++loadSeq;
		const root = workspacePath;
		if (!root) return;

		if (initial) loading = true;
		else refreshing = true;
		error = '';

		try {
			const [details, diff] = await Promise.all([
				getGitPrView(root, number),
				getGitPrDiff(root, number, $hideWhitespaceChanges) as Promise<{ files?: DiffFile[] }>
			]);
			const previousExpansion = new Map(reviewFiles.map((file) => [file.key, file.expanded]));
			const nextFiles = (diff.files ?? []).map((diffFile) => {
				const counts = countDiffStats([diffFile]);
				return {
					key: `pr:${number}:${diffFile.path}`,
					path: diffFile.path,
					status: 'modified',
					staged: false,
					binary: diffFile.hunks.length === 0,
					diffFiles: [diffFile],
					additions: counts.additions,
					deletions: counts.deletions,
					expanded: previousExpansion.get(`pr:${number}:${diffFile.path}`) ?? true
				};
			});
			if (seq !== loadSeq || root !== workspacePath) return;
			pr = details;
			reviewFiles = nextFiles;
		} catch (e) {
			if (seq !== loadSeq) return;
			error = e instanceof Error ? e.message : $t('git.loadChangesFailed');
			pr = null;
			reviewFiles = [];
		} finally {
			if (seq === loadSeq) {
				loading = false;
				refreshing = false;
			}
		}
	}

	async function refreshReview(initial = false) {
		const target = reviewTarget;
		if (target?.kind === 'pr') {
			await fetchPrDiffs(target.number, initial);
			return;
		}
		await gitStatusStore.refresh({ force: true });
	}

	function fileKey(file: GitFile): string {
		return `${file.staged ? 'staged' : 'unstaged'}:${file.status}:${file.path}`;
	}

	function toggleFile(key: string) {
		reviewFiles = reviewFiles.map((file) =>
			file.key === key ? { ...file, expanded: !file.expanded } : file
		);
	}

	function toggleAll() {
		const expand = !anyExpanded;
		reviewFiles = reviewFiles.map((file) => ({ ...file, expanded: expand }));
	}

	function openFile(path: string) {
		const fullPath = workspacePath.replace(/\/$/, '') + '/' + path;
		openFileTab(fullPath);
		gitReviewOpen.set(null);
	}

	function closeReview() {
		gitReviewOpen.set(null);
	}

	async function commentOnPr(event: 'comment' | 'request_changes' | 'approve') {
		if (!pr) return;
		const defaultBody =
			event === 'approve'
				? 'Looks good.'
				: event === 'request_changes'
					? 'Please address this.'
					: '';
		const body = window.prompt(
			event === 'approve'
				? 'Approval note'
				: event === 'request_changes'
					? 'Changes requested'
					: 'Comment',
			defaultBody
		);
		if (body === null) return;
		try {
			if (event === 'comment') await gitPrComment(workspacePath, pr.number, body);
			else await gitPrReview(workspacePath, pr.number, event, body);
			await refreshReview(false);
		} catch (e) {
			error = e instanceof Error ? e.message : 'GitHub CLI action failed';
		}
	}

	function askCodexAboutPr() {
		if (!pr || !workspacePath) return;
		const draft = [
			`Review this pull request and help me decide what to do next.`,
			``,
			`PR: #${pr.number} ${pr.title}`,
			`URL: ${pr.url}`,
			`Branch: ${pr.headRefName || '?'} -> ${pr.baseRefName || '?'}`,
			`Files: ${pr.changedFiles ?? totalChanges}, +${pr.additions ?? totalAdditions}/-${pr.deletions ?? totalDeletions}`
		].join('\n');
		sessionStorage.setItem(`cptr:intent:chatDraft:${workspacePath}`, draft);
		openChatTab();
		closeReview();
	}

	function pathParts(path: string): { dir: string; name: string } {
		const slash = path.lastIndexOf('/');
		if (slash < 0) return { dir: '', name: path };
		return { dir: path.slice(0, slash + 1), name: path.slice(slash + 1) };
	}

	function statusChar(status: string): string {
		const chars: Record<string, string> = {
			added: 'A',
			untracked: 'U',
			modified: 'M',
			deleted: 'D',
			renamed: 'R'
		};
		return chars[status] ?? '?';
	}
</script>

<div
	class="flex h-full flex-col overflow-hidden bg-white text-gray-900 dark:bg-black dark:text-gray-100"
>
	{#if loading && !gitStatus && !isPrReview}
		<div class="flex h-full items-center justify-center">
			<Spinner size={16} />
		</div>
	{:else if error && !gitStatus && !isPrReview}
		<div class="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
			<Icon name="git-diff" size={24} class="text-gray-300 dark:text-gray-700" />
			<p class="text-xs font-medium text-gray-700 dark:text-gray-300">
				{$t('git.unableToLoadChanges')}
			</p>
			<p class="max-w-sm text-[0.6875rem] text-gray-400 dark:text-gray-600">{error}</p>
		</div>
	{:else if !isPrReview && !gitStatus?.is_repo}
		<div class="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
			<Icon name="git-branch" size={24} class="text-gray-300 dark:text-gray-700" />
			<p class="text-xs text-gray-400 dark:text-gray-600">{$t('git.notARepo')}</p>
		</div>
	{:else}
		<header
			class="flex h-9 shrink-0 items-center gap-2 border-b border-gray-200 px-3 dark:border-white/6"
		>
			<div class="flex min-w-0 flex-1 items-center gap-1.5">
				<Icon
					name={isPrReview ? 'git-diff' : 'git-branch'}
					size={14}
					class="shrink-0 text-gray-400 dark:text-gray-600"
				/>
				<span class="truncate font-mono text-xs font-medium text-gray-700 dark:text-gray-300">
					{#if isPrReview}
						{pr ? `#${pr.number}` : 'Pull request'}
					{:else}
						{gitStatus?.branch || 'HEAD'}
					{/if}
				</span>
				{#if isPrReview && pr}
					<span class="truncate text-xs text-gray-600 dark:text-gray-300">{pr.title}</span>
					<span
						class="hidden sm:inline truncate font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600"
						>{pr.headRefName} → {pr.baseRefName}</span
					>
				{:else if gitStatus?.upstream}
					<Icon name="chevron-right" size={12} class="shrink-0 text-gray-300 dark:text-gray-700" />
					<span class="truncate font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600"
						>{gitStatus.upstream}</span
					>
				{/if}
				{#if !isPrReview && gitStatus && gitStatus.ahead > 0}
					<span class="font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
						>+{gitStatus.ahead}</span
					>
				{/if}
				{#if !isPrReview && gitStatus && gitStatus.behind > 0}
					<span class="font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
						>-{gitStatus.behind}</span
					>
				{/if}
				{#if totalChanges > 0}
					<span class="ml-1 font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
						>{$t('git.changedCount', { count: totalChanges })}</span
					>
					{#if totalAdditions || totalDeletions}
						<span class="ml-2 font-mono text-[0.625rem] text-green-600 dark:text-green-400"
							>+{totalAdditions}</span
						>
						<span class="font-mono text-[0.625rem] text-red-500 dark:text-red-400"
							>-{totalDeletions}</span
						>
					{/if}
				{/if}
			</div>

			{#if isPrReview && pr}
				<button
					class="hidden sm:flex h-6 items-center rounded-md px-2 text-[0.6875rem] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-gray-200"
					onclick={askCodexAboutPr}
				>
					Draft chat
				</button>
				<button
					class="hidden sm:flex h-6 items-center rounded-md px-2 text-[0.6875rem] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-gray-200"
					onclick={() => commentOnPr('comment')}
				>
					Comment
				</button>
				<button
					class="hidden sm:flex h-6 items-center rounded-md px-2 text-[0.6875rem] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-gray-200"
					onclick={() => commentOnPr('approve')}
				>
					Approve
				</button>
				<button
					class="hidden sm:flex h-6 items-center rounded-md px-2 text-[0.6875rem] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-gray-200"
					onclick={() => commentOnPr('request_changes')}
				>
					Request changes
				</button>
				<a
					href={pr.url}
					target="_blank"
					rel="noopener noreferrer"
					class="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300"
					use:tooltip={'Open on GitHub'}
				>
					<Icon name="external-link" size={12} />
				</a>
			{/if}
			<button
				bind:this={diffSettingsButtonEl}
				class="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300"
				onclick={() => (showDiffSettings = true)}
				aria-label={$t('diff.settings')}
				use:tooltip={$t('diff.settings')}
			>
				<Icon name="settings" size={13} />
			</button>

			<button
				class="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300"
				onclick={closeReview}
				aria-label={$t('common.close')}
				use:tooltip={$t('common.close')}
			>
				<Icon name="xmark" size={12} />
			</button>
			<button
				class="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300"
				onclick={() => refreshReview(false)}
				disabled={refreshing}
				aria-label={$t('a11y.refreshChanges')}
				use:tooltip={$t('a11y.refreshChanges')}
			>
				<Icon name="refresh" size={12} class={refreshing ? 'animate-spin' : ''} />
			</button>
		</header>

		{#if showDiffSettings && diffSettingsButtonEl}
			<DiffSettingsMenu anchor={diffSettingsButtonEl} onclose={() => (showDiffSettings = false)} />
		{/if}

		{#if totalChanges > 0}
			<div
				class="flex h-8 shrink-0 items-center gap-2 border-b border-gray-200 px-3 dark:border-white/6"
			>
				<button
					class="flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[0.6875rem] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-gray-300"
					onclick={toggleAll}
				>
					<Icon name={anyExpanded ? 'chevron-down' : 'chevron-right'} size={12} />
					<span>{anyExpanded ? $t('git.collapseAll') : $t('git.expandAll')}</span>
				</button>
				<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{isPrReview ? 'Pull request changes' : $t('git.uncommittedChanges')}
				</span>
				<span class="ml-auto text-[0.6875rem] text-gray-400 dark:text-gray-600"
					>{$t('git.change', { count: totalChanges })}</span
				>
				{#if totalAdditions || totalDeletions}
					<span class="ml-2 font-mono text-[0.6875rem] text-green-600 dark:text-green-400"
						>+{totalAdditions}</span
					>
					<span class="font-mono text-[0.6875rem] text-red-500 dark:text-red-400"
						>-{totalDeletions}</span
					>
				{/if}
			</div>
		{/if}

		<div class="min-h-0 flex-1 overflow-y-auto">
			{#if loading}
				<div class="flex h-full items-center justify-center">
					<Spinner size={16} />
				</div>
			{:else if error}
				<div class="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
					<Icon name="git-diff" size={24} class="text-gray-300 dark:text-gray-700" />
					<p class="text-xs font-medium text-gray-700 dark:text-gray-300">
						{$t('git.unableToLoadChanges')}
					</p>
					<p class="max-w-sm text-[0.6875rem] text-gray-400 dark:text-gray-600">{error}</p>
				</div>
			{:else if totalChanges === 0}
				<div class="flex h-full items-center justify-center px-6">
					<div class="flex -translate-y-8 flex-col items-center gap-2 text-center">
						<Icon
							name="git-diff"
							size={30}
							strokeWidth={1.2}
							class="text-gray-300 dark:text-gray-700"
						/>
						<h2 class="text-sm font-medium text-gray-700 dark:text-gray-300">
							{isPrReview ? 'No pull request changes' : $t('git.noFileChangesYet')}
						</h2>
						<p class="text-xs text-gray-400 dark:text-gray-600">
							{isPrReview ? 'GitHub returned an empty diff.' : $t('git.projectChangesHint')}
						</p>
					</div>
				</div>
			{:else}
				<div class="p-1">
					{#each reviewFiles as file (file.key)}
						{@const parts = pathParts(file.path)}
						<section class="overflow-hidden rounded-lg">
							<button
								class="flex h-8 w-full min-w-0 items-center gap-1.5 rounded-lg px-2 text-left transition-colors hover:bg-gray-100 dark:hover:bg-white/4"
								onclick={() => toggleFile(file.key)}
							>
								<Icon
									name="chevron-right"
									size={12}
									class="shrink-0 text-gray-400 transition-transform dark:text-gray-600 {file.expanded
										? 'rotate-90'
										: ''}"
								/>
								<Icon name="git-diff" size={13} class="shrink-0 text-gray-400 dark:text-gray-600" />
								<div class="flex min-w-0 flex-1 items-baseline gap-2">
									<!-- svelte-ignore a11y_no_static_element_interactions -->
									<span
										class="truncate text-xs font-medium text-gray-800 dark:text-gray-200 hover:underline"
										onclick={(e) => {
											e.stopPropagation();
											openFile(file.path);
										}}>{parts.name}</span
									>
									{#if parts.dir}
										<span
											class="hidden truncate text-[0.6875rem] text-gray-400 dark:text-gray-600 sm:inline"
											>{parts.dir}</span
										>
									{/if}
								</div>
								{#if file.binary}
									<span
										class="shrink-0 font-mono text-[0.6875rem] font-medium text-gray-500 dark:text-gray-400"
										>{statusChar(file.status)}</span
									>
								{:else}
									<span
										class="shrink-0 font-mono text-[0.6875rem] font-medium text-green-600 dark:text-green-400"
										>+{file.additions}</span
									>
									<span
										class="shrink-0 font-mono text-[0.6875rem] font-medium text-red-500 dark:text-red-400"
										>-{file.deletions}</span
									>
								{/if}

								{#if !isPrReview}
									<span
										class="shrink-0 rounded px-1.5 py-0.5 text-[0.625rem] font-medium text-rose-500 bg-rose-50 dark:bg-rose-500/10 dark:text-rose-300"
									>
										{file.staged ? $t('git.staged') : $t('git.uncommittedChanges')}
									</span>
								{/if}
							</button>

							{#if file.expanded}
								<div
									class="mb-1 overflow-x-auto border-y border-gray-100 bg-white font-mono text-[0.6875rem] leading-[1.125rem] dark:border-white/4 dark:bg-black"
								>
									<div class="diff-content" class:diff-content-split={$diffDisplayMode === 'split'}>
										{#if file.diffFiles.some((diffFile) => diffFile.hunks.length > 0)}
											{#each file.diffFiles as diffFile}
												{#if file.diffFiles.length > 1}
													<div
														class="sticky top-0 z-10 border-b border-gray-100 bg-gray-50 px-2 py-1 text-[0.625rem] font-medium text-gray-500 dark:border-white/4 dark:bg-white/3 dark:text-gray-400"
													>
														{diffFile.path}
													</div>
												{/if}
												<DiffHunkList hunks={diffFile.hunks} path={diffFile.path} showNumbers />
											{/each}
										{:else}
											<div
												class="px-3 py-8 text-center text-[0.6875rem] text-gray-400 dark:text-gray-600"
											>
												{$t('git.noTextualDiff')}
											</div>
										{/if}
									</div>
								</div>
							{/if}
						</section>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</div>

<style>
	.diff-content {
		width: max-content;
		min-width: 100%;
	}

	.diff-content-split {
		width: 100%;
	}
</style>
