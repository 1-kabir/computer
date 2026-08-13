<script lang="ts">
	import { goto } from '$app/navigation';
	import {
		activeWorkspace,
		addWorkspace,
		openChatTab,
		openFileTab
	} from '$lib/stores';
	import {
		getGitCompareDiff,
		getGitCurrentPr,
		getGitLog,
		getGitDiff,
		getGitPrCapabilities,
		getGitPrChecks,
		getGitPrDiff,
		getGitPrList,
		getGitPrView,
		getGitShow,
		getGitBranches,
		getGitWorktrees,
		getGitStatusFresh,
		getGitStashes,
		gitPrCheckout,
		gitPrClose,
		gitPrComment,
		gitPrCreate,
		gitPrEdit,
		gitPrMerge,
		gitPrReady,
		gitPrReopen,
		gitPrReview,
		gitPrUpdateBranch,
		stageFiles,
		unstageFiles,
		discardChanges,
		gitCommit,
		generateGitCommitMessage,
		gitFetch,
		gitPull,
		gitPush,
		gitUncommit,
		gitStash,
		gitUnstash,
		checkoutBranch,
		createGitBranch,
		createGitWorktree,
		deleteGitBranch,
		renameGitBranch,
		type GitPr,
		type GitPrCapabilities,
		type GitPrCheck
	} from '$lib/apis/git';
	import { gitStatusStore } from '$lib/stores/gitStatus.svelte';
	import { diffDisplayMode, hideWhitespaceChanges } from '$lib/stores/gitDiffSettings';
	import Icon from './Icon.svelte';
	import DropdownMenu from './DropdownMenu.svelte';
	import Modal from './Modal.svelte';
	import { tooltip } from '$lib/tooltip';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import DiffSettingsMenu from './DiffSettingsMenu.svelte';
	import { countDiffStats, type DiffFile } from '$lib/utils/diff';
	import DiffHunkList from './DiffHunkList.svelte';

	type GitFile = {
		path: string;
		status: string;
		staged: boolean;
		unstaged?: boolean;
		staged_status?: string;
		unstaged_status?: string;
		binary?: boolean;
		additions?: number;
		deletions?: number;
	};
	type Commit = { hash: string; short_hash: string; author: string; date: string; message: string };
	type BranchItem = {
		name: string;
		is_current?: boolean;
		is_local?: boolean;
		is_remote?: boolean;
		upstream?: string | null;
	};
	type BranchData = {
		current: string;
		local: string[];
		remote: string[];
		all?: BranchItem[];
	};
	type WorktreeItem = {
		path: string;
		target_path?: string;
		branch: string;
		head: string;
		is_current?: boolean;
		is_detached?: boolean;
		is_bare?: boolean;
	};
	type WorktreeData = {
		repo_root: string;
		current: string;
		worktrees: WorktreeItem[];
	};

	let expanded = $state(false);
	type GitBarView = 'changes' | 'history' | 'pullRequests';

	let view = $state<GitBarView>('changes');
	let showDiff = $state(false);
	let showBranches = $state(false);
	let showWorktrees = $state(false);
	let showDiffSettings = $state(false);
	let branchBtnEl = $state<HTMLButtonElement | undefined>();
	let worktreeBtnEl = $state<HTMLButtonElement | undefined>();
	let diffSettingsBtnEl = $state<HTMLButtonElement | undefined>();
	let branchSearchInputEl = $state<HTMLInputElement | undefined>();
	let worktreeSearchInputEl = $state<HTMLInputElement | undefined>();
	let newBranchInputEl = $state<HTMLInputElement | undefined>();
	let newWorktreeInputEl = $state<HTMLInputElement | undefined>();
	let commits = $state<Commit[]>([]);
	let branchData = $state<BranchData | null>(null);
	let worktreeData = $state<WorktreeData | null>(null);
	let branchSearch = $state('');
	let worktreeSearch = $state('');
	let newBranchName = $state('');
	let newWorktreeBranch = $state('');
	let creatingBranch = $state(false);
	let creatingWorktree = $state(false);
	let pendingCheckoutBranch = $state<string | null>(null);
	let pendingCreateBranch = $state<string | null>(null);
	let pendingCheckoutPr = $state<GitPr | null>(null);
	let branchActionMenu = $state<{ branch: BranchItem; anchor: HTMLElement } | null>(null);
	let selectedFile = $state<string | null>(null);
	let selectedCommit = $state<Commit | null>(null);
	let selectedPr = $state<GitPr | null>(null);
	let selectedPrDetail = $state<GitPr | null>(null);
	let fileDiff = $state<DiffFile[]>([]);
	let prCapabilities = $state<GitPrCapabilities | null>(null);
	let currentPr = $state<GitPr | null>(null);
	let prs = $state<GitPr[]>([]);
	type PrState = 'open' | 'closed' | 'all';
	type PrSubview = 'summary' | 'code' | 'checks' | 'activity';
	const prStates: PrState[] = ['open', 'closed', 'all'];
	const prSubviews: PrSubview[] = ['summary', 'code', 'checks', 'activity'];
	let prState = $state<PrState>('open');
	let prSubview = $state<PrSubview>('summary');
	let prSearch = $state('');
	let prLimit = $state(30);
	let prLoading = $state(false);
	let prDiffLoading = $state(false);
	let prDetailLoading = $state(false);
	let prChecks = $state<GitPrCheck[]>([]);
	let prError = $state('');
	let prCreateTitle = $state('');
	let prCreateBody = $state('');
	let prCreateBase = $state('');
	let prCreateHead = $state('');
	let prCreateDraft = $state(false);
	let prCreateDiff = $state<DiffFile[]>([]);
	let prCreateDiffLoading = $state(false);
	let prCreateDiffError = $state('');
	let prReviewBody = $state('');
	let prEditBase = $state('');
	let prEditReviewers = $state('');
	let prEditAssignees = $state('');
	let prEditLabels = $state('');
	let prEditMilestone = $state('');
	let commitSummary = $state('');
	let commitDescription = $state('');
	let actionMsg = $state('');
	let loading = $state(false);
	let generatingCommitMessage = $state(false);
	let panelHeight = $state(280);
	let resizing = $state(false);
	let containerEl = $state<HTMLElement | null>(null);

	let gitStatus = $derived(
		gitStatusStore.status as {
			is_repo: boolean;
			branch: string;
			upstream: string;
			remote_url: string;
			ahead: number;
			behind: number;
			files: GitFile[];
		} | null
	);

	const workspacePath = $derived($activeWorkspace?.path ?? '');
	const stagedFiles = $derived((gitStatus?.files ?? []).filter((f) => f.staged));
	const unstagedFiles = $derived((gitStatus?.files ?? []).filter((f) => !f.staged));
	const totalChanges = $derived((gitStatus?.files ?? []).length);
	const totalAdditions = $derived(
		(gitStatus?.files ?? []).reduce((sum, file) => sum + (file.additions ?? 0), 0)
	);
	const totalDeletions = $derived(
		(gitStatus?.files ?? []).reduce((sum, file) => sum + (file.deletions ?? 0), 0)
	);
	const selectedDiffStats = $derived(countDiffStats(fileDiff));
	const prCreateDiffStats = $derived(countDiffStats(prCreateDiff));
	const allStaged = $derived(totalChanges > 0 && unstagedFiles.length === 0);
	const someStaged = $derived(stagedFiles.length > 0 && unstagedFiles.length > 0);
	const selectedPrInfo = $derived(selectedPrDetail ?? selectedPr);
	const prBaseBranchOptions = $derived.by(() => branchOptions(prCreateBase, true));
	const prCompareBranchOptions = $derived.by(() => branchOptions(prCreateHead, false));

	// Branch has never been pushed to remote
	const needsPublish = $derived(!gitStatus?.upstream);
	const unpushedCount = $derived(needsPublish ? (commits?.length ?? 0) : (gitStatus?.ahead ?? 0));
	const filteredBranches = $derived.by(() => {
		const branches = branchData?.all ?? [];
		const query = branchSearch.trim().toLowerCase();
		if (!query) return branches;
		return branches.filter((branch) => branch.name.toLowerCase().includes(query));
	});
	const filteredWorktrees = $derived.by(() => {
		const worktrees = worktreeData?.worktrees ?? [];
		const query = worktreeSearch.trim().toLowerCase();
		if (!query) return worktrees;
		return worktrees.filter((worktree) =>
			`${worktreeLabel(worktree)} ${worktree.path}`.toLowerCase().includes(query)
		);
	});

	// Convert git remote URL to browser URL
	const remoteWebUrl = $derived.by(() => {
		const url = gitStatus?.remote_url;
		if (!url) return '';
		// SSH: git@github.com:user/repo.git
		const sshMatch = url.match(/^git@([^:]+):(.+?)(\.git)?$/);
		if (sshMatch) return `https://${sshMatch[1]}/${sshMatch[2]}`;
		// HTTPS: https://github.com/user/repo.git
		return url.replace(/\.git$/, '');
	});

	const remotePlatform = $derived.by(() => {
		if (!remoteWebUrl) return '';
		if (remoteWebUrl.includes('github.com')) return 'GitHub';
		if (remoteWebUrl.includes('gitlab.com') || remoteWebUrl.includes('gitlab')) return 'GitLab';
		if (remoteWebUrl.includes('bitbucket')) return 'Bitbucket';
		try {
			return new URL(remoteWebUrl).hostname;
		} catch {
			return 'Remote';
		}
	});

	const githubRepoSlug = $derived.by(() => {
		if (!remoteWebUrl || !remoteWebUrl.includes('github.com')) return '';
		try {
			const url = new URL(remoteWebUrl);
			return url.pathname.replace(/^\/+/, '').replace(/\.git$/, '');
		} catch {
			return remoteWebUrl.replace(/^https:\/\/github\.com\//, '').replace(/\.git$/, '');
		}
	});

	const prTabVisible = $derived(
		prCapabilities?.is_github === true && prCapabilities?.gh_installed === true
	);
	const prReady = $derived(prTabVisible && prCapabilities?.authenticated === true);
	const prCount = $derived(prs.length);

	// Clear stale selection when file is no longer in the changed list
	$effect(() => {
		if (view === 'changes' && selectedFile) {
			const stillExists = (gitStatus?.files ?? []).some((f) => f.path === selectedFile);
			if (!stillExists) {
				selectedFile = null;
				fileDiff = [];
				showDiff = false;
			}
		}
	});

	let _prevPrRoot = '';
	let _prevPrRemote = '';
	$effect(() => {
		const root = workspacePath;
		const remote = remoteWebUrl;
		if (!root || !remote || !remote.includes('github.com')) {
			prCapabilities = null;
			currentPr = null;
			prs = [];
			selectedPr = null;
			selectedPrDetail = null;
			return;
		}
		if (root === _prevPrRoot && remote === _prevPrRemote) return;
		_prevPrRoot = root;
		_prevPrRemote = remote;
		loadPrCapabilities();
	});

	let _prevHideWhitespace = false;
	$effect(() => {
		const hideWhitespace = $hideWhitespaceChanges;
		if (hideWhitespace === _prevHideWhitespace) return;
		_prevHideWhitespace = hideWhitespace;
		if (!expanded || !showDiff) return;
		reloadSelectedDiff();
	});

	let prCreateDiffTimer: ReturnType<typeof setTimeout> | null = null;
	let prCreateDiffKey = '';
	$effect(() => {
		const base = prCreateBase.trim();
		const head = prCreateHead.trim();
		const ignoreWhitespace = $hideWhitespaceChanges;
		const active =
			expanded &&
			view === 'pullRequests' &&
			prCapabilities?.authenticated === true &&
			!selectedPr;
		if (!active || !workspacePath || !base || !head) {
			if (prCreateDiffTimer) clearTimeout(prCreateDiffTimer);
			prCreateDiff = [];
			prCreateDiffError = '';
			prCreateDiffLoading = false;
			prCreateDiffKey = '';
			return;
		}

		const key = `${workspacePath}\0${base}\0${head}\0${ignoreWhitespace}`;
		if (key === prCreateDiffKey) return;
		prCreateDiffKey = key;
		prCreateDiffLoading = true;
		prCreateDiffError = '';
		if (prCreateDiffTimer) clearTimeout(prCreateDiffTimer);
		prCreateDiffTimer = setTimeout(() => {
			void loadCreateCompareDiff(base, head, ignoreWhitespace, key);
		}, 250);
	});

	// Auto-select first file
	$effect(() => {
		if (
			expanded &&
			view === 'changes' &&
			totalChanges > 0 &&
			!selectedFile &&
			window.innerWidth >= 768
		) {
			const f = [...stagedFiles, ...unstagedFiles][0];
			if (f) selectFile(f.path, f.staged, f.status === 'untracked');
		}
	});

	async function refresh({ force = false }: { force?: boolean } = {}) {
		await gitStatusStore.refresh({ force });
	}

	async function selectFile(path: string, staged: boolean, untracked: boolean = false) {
		selectedFile = path;
		selectedCommit = null;
		showDiff = true;
		try {
			const params = new URLSearchParams({
				root: workspacePath,
				file: path,
				staged: String(staged),
				ignore_whitespace: String($hideWhitespaceChanges)
			});
			if (untracked) params.set('untracked', 'true');
			const d = (await getGitDiff(params.toString())) as { files?: DiffFile[] };
			fileDiff = d.files ?? [];
		} catch {
			fileDiff = [];
		}
	}

	async function selectCommit(c: Commit) {
		selectedCommit = c;
		selectedFile = null;
		showDiff = true;
		try {
			const d = (await getGitShow(workspacePath, c.hash, $hideWhitespaceChanges)) as {
				diff?: { files?: DiffFile[] };
			};
			fileDiff = d.diff?.files ?? [];
		} catch {
			fileDiff = [];
		}
	}

	function backToList() {
		showDiff = false;
	}

	function reloadSelectedDiff() {
		if (view === 'pullRequests' && selectedPr) {
			loadPullRequestDiff(selectedPr);
			return;
		}
		if (selectedCommit) {
			selectCommit(selectedCommit);
			return;
		}
		if (!selectedFile) return;
		const file = (gitStatus?.files ?? []).find((item) => item.path === selectedFile);
		if (file) selectFile(file.path, file.staged, file.status === 'untracked');
	}

	async function loadHistory() {
		try {
			commits = (await getGitLog(workspacePath, 50)) as Commit[];
		} catch {
			commits = [];
		}
	}

	async function loadBranches() {
		try {
			branchData = (await getGitBranches(workspacePath)) as BranchData;
		} catch {
			branchData = { current: '', local: [], remote: [], all: [] };
		}
	}

	async function loadWorktrees() {
		try {
			worktreeData = (await getGitWorktrees(workspacePath)) as WorktreeData;
		} catch {
			worktreeData = { repo_root: '', current: '', worktrees: [] };
		}
	}

	async function toggleBranches(e: MouseEvent) {
		e.stopPropagation();
		if (showBranches) {
			showBranches = false;
			return;
		}
		showBranches = true;
		showWorktrees = false;
		branchSearch = '';
		creatingBranch = false;
		newBranchName = '';
		branchData = null;
		await loadBranches();
		setTimeout(() => branchSearchInputEl?.focus(), 0);
	}

	async function toggleWorktrees(e: MouseEvent) {
		e.stopPropagation();
		if (showWorktrees) {
			showWorktrees = false;
			return;
		}
		showWorktrees = true;
		showBranches = false;
		worktreeSearch = '';
		creatingWorktree = false;
		newWorktreeBranch = '';
		worktreeData = null;
		await loadWorktrees();
		setTimeout(() => worktreeSearchInputEl?.focus(), 0);
	}

	async function loadPrCapabilities() {
		try {
			prCapabilities = await getGitPrCapabilities(workspacePath);
			if (prCapabilities.default_branch && !prCreateBase) {
				prCreateBase = prCapabilities.default_branch;
			}
			if (prCapabilities.authenticated) await loadPullRequests();
		} catch {
			prCapabilities = null;
		}
	}

	async function loadPullRequests() {
		if (!workspacePath || !prCapabilities?.authenticated) return;
		prLoading = true;
		prError = '';
		try {
			const [current, list] = await Promise.all([
				getGitCurrentPr(workspacePath),
				getGitPrList(workspacePath, prState, 'all', prSearch, prLimit)
			]);
			currentPr = current.found ? (current.pr ?? null) : null;
			prs = list.items ?? [];
			if (selectedPr) {
				selectedPr =
					[currentPr, ...prs].find((pr) => pr?.number === selectedPr?.number) ?? null;
				if (!selectedPr) {
					selectedPrDetail = null;
					prChecks = [];
				} else {
					await loadPullRequestDetail(selectedPr);
					if (showDiff && prSubview === 'code') await loadPullRequestDiff(selectedPr);
					if (prSubview === 'checks') await loadPullRequestChecks(selectedPr);
				}
			}
			if (!currentPr) seedCreatePullRequest();
		} catch (e) {
			prError = e instanceof Error ? e.message : 'Could not load pull requests';
			currentPr = null;
			prs = [];
			selectedPr = null;
			selectedPrDetail = null;
			prChecks = [];
		} finally {
			prLoading = false;
		}
	}

	function switchView(v: GitBarView) {
		view = v;
		showDiff = false;
		selectedFile = null;
		selectedCommit = null;
		if (v !== 'pullRequests') selectedPr = null;
		if (v !== 'pullRequests') selectedPrDetail = null;
		fileDiff = [];
		if (v === 'history') loadHistory();
		if (v === 'pullRequests') {
			if (!branchData) loadBranches();
			if (!prCapabilities) loadPrCapabilities();
			else if (prCapabilities.authenticated) loadPullRequests();
		}
	}

	async function toggleStage(e: Event, file: GitFile) {
		e.stopPropagation();
		if (file.staged) {
			await unstageFiles(workspacePath, [file.path]);
		} else {
			await stageFiles(workspacePath, [file.path]);
		}
		await refresh();
	}

	async function toggleAll() {
		if (allStaged) {
			await unstageFiles(
				workspacePath,
				stagedFiles.map((f) => f.path)
			);
		} else {
			await stageFiles(
				workspacePath,
				unstagedFiles.map((f) => f.path)
			);
		}
		await refresh();
	}

	async function doCommit() {
		if (!commitSummary.trim() || !stagedFiles.length) return;
		loading = true;
		try {
			await stageFiles(
				workspacePath,
				stagedFiles.map((f) => f.path)
			);
			const msg = commitDescription.trim()
				? `${commitSummary.trim()}\n\n${commitDescription.trim()}`
				: commitSummary.trim();
			await gitCommit(workspacePath, msg);
			commitSummary = '';
			commitDescription = '';
			flash($t('git.committed'));
			selectedFile = null;
			fileDiff = [];
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Commit failed');
		} finally {
			loading = false;
			await refresh();
		}
	}

	async function generateCommitMessage() {
		if (!stagedFiles.length || generatingCommitMessage) return;
		generatingCommitMessage = true;
		try {
			const message = await generateGitCommitMessage(workspacePath);
			commitSummary = message.summary;
			commitDescription = message.description;
		} catch (e) {
			flash(e instanceof Error ? e.message : $t('git.generateMessageFailed'));
		} finally {
			generatingCommitMessage = false;
		}
	}

	async function doUncommit() {
		loading = true;
		try {
			const d = await gitUncommit(workspacePath);
			flash($t('git.uncommitted'));
			switchView('changes');
		} catch (e) {
			flash(e instanceof Error ? e.message : $t('git.uncommitFailed'));
		}
		loading = false;
		await refresh();
	}

	async function doPull() {
		loading = true;
		try {
			const d = await gitPull(workspacePath);
			flash(d.ok ? $t('git.pulled') : d.message);
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Pull failed');
		} finally {
			loading = false;
			await refresh({ force: true });
		}
	}

	async function doFetch() {
		loading = true;
		try {
			const d = (await gitFetch(workspacePath)) as { ok?: boolean; message?: string };
			flash(d.ok ? d.message || $t('git.fetch') : d.message || 'Fetch failed');
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Fetch failed');
		} finally {
			loading = false;
			await refresh({ force: true });
			if (showBranches) await loadBranches();
			if (showWorktrees) await loadWorktrees();
			if (view === 'history') await loadHistory();
			if (view === 'pullRequests') await loadPullRequests();
		}
	}

	async function doPush() {
		loading = true;
		try {
			const d = await gitPush(workspacePath, {
				set_upstream: needsPublish,
				branch: needsPublish ? gitStatus?.branch : undefined
			});
			flash(d.ok ? $t('git.pushed') : d.message);
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Push failed');
		} finally {
			loading = false;
			await refresh({ force: true });
			if (prCapabilities?.authenticated) await loadPullRequests();
		}
	}

	async function switchBranch(branch: string) {
		if (branch === gitStatus?.branch) {
			showBranches = false;
			return;
		}
		if (totalChanges > 0) {
			pendingCheckoutBranch = branch;
			showBranches = false;
			return;
		}
		await performBranchCheckout(branch, 'bring');
	}

	async function switchWorktree(worktree: WorktreeItem) {
		if (worktree.is_current) {
			showWorktrees = false;
			return;
		}
		const target = worktree.target_path || worktree.path;
		addWorkspace(target);
		showWorktrees = false;
		creatingWorktree = false;
		newWorktreeBranch = '';
		await goto(`/?workspace=${encodeURIComponent(target)}`);
	}

	async function restoreStashedChangesForBranch(branch: string) {
		const stashes = (await getGitStashes(workspacePath)) as { message?: string }[];
		const index = stashes.findIndex((stash) => {
			const message = stash.message ?? '';
			return (
				message.includes(`cptr-branch-switch:${branch}`) ||
				message.includes(`Changes on ${branch} before checking out`)
			);
		});
		if (index < 0) return;

		const freshStatus = (await getGitStatusFresh(workspacePath)) as { files?: GitFile[] };
		if ((freshStatus.files ?? []).length > 0) return;

		const result = (await gitUnstash(workspacePath, index)) as { ok?: boolean; message?: string };
		if (result.ok) {
			flash(`Restored changes for ${branch}`);
		} else {
			flash(result.message || `Could not restore changes for ${branch}`);
		}
	}

	async function performBranchCheckout(branch: string, changeMode: 'bring' | 'leave') {
		loading = true;
		try {
			if (changeMode === 'leave') {
				const currentBranch = gitStatus?.branch || 'current branch';
				const message = `cptr-branch-switch:${currentBranch}`;
				const stashResult = (await gitStash(workspacePath, message)) as {
					ok?: boolean;
					message?: string;
				};
				if (!stashResult.ok) {
					flash(stashResult.message || $t('git.noChangesStashed'));
					return;
				}
			}
			await checkoutBranch(workspacePath, branch);
			pendingCheckoutBranch = null;
			await restoreStashedChangesForBranch(branch);
		} catch (e) {
			flash(e instanceof Error ? e.message : $t('git.switchBranchFailed'));
		} finally {
			loading = false;
			await refresh();
			if (prCapabilities?.authenticated) await loadPullRequests();
		}
	}

	async function checkoutPullRequest(pr: GitPr) {
		if (totalChanges > 0) {
			pendingCheckoutPr = pr;
			return;
		}
		await performPrCheckout(pr, 'bring');
	}

	async function performPrCheckout(pr: GitPr, changeMode: 'bring' | 'leave') {
		loading = true;
		try {
			if (changeMode === 'leave') {
				const currentBranch = gitStatus?.branch || 'current branch';
				const stashResult = (await gitStash(
					workspacePath,
					`cptr-branch-switch:${currentBranch}`
				)) as {
					ok?: boolean;
					message?: string;
				};
				if (!stashResult.ok) {
					flash(stashResult.message || $t('git.noChangesStashed'));
					return;
				}
			}
			const result = await gitPrCheckout(workspacePath, pr.number);
			flash(result.ok ? `Checked out #${pr.number}` : result.message);
			pendingCheckoutPr = null;
			await refresh({ force: true });
			await loadBranches();
			await loadPullRequests();
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Checkout failed');
		} finally {
			loading = false;
		}
	}

	async function loadPullRequestDiff(pr: GitPr) {
		prDiffLoading = true;
		try {
			const d = (await getGitPrDiff(workspacePath, pr.number, $hideWhitespaceChanges)) as {
				files?: DiffFile[];
			};
			fileDiff = d.files ?? [];
		} catch (e) {
			fileDiff = [];
			flash(e instanceof Error ? e.message : 'Could not load pull request diff');
		} finally {
			prDiffLoading = false;
		}
	}

	async function loadCreateCompareDiff(
		base: string,
		head: string,
		ignoreWhitespace: boolean,
		key: string
	) {
		try {
			const d = (await getGitCompareDiff(workspacePath, base, head, ignoreWhitespace)) as {
				files?: DiffFile[];
			};
			if (key !== prCreateDiffKey) return;
			prCreateDiff = d.files ?? [];
			prCreateDiffError = '';
		} catch (e) {
			if (key !== prCreateDiffKey) return;
			prCreateDiff = [];
			prCreateDiffError = e instanceof Error ? e.message : 'Could not load compare diff';
		} finally {
			if (key === prCreateDiffKey) prCreateDiffLoading = false;
		}
	}

	async function selectPullRequest(pr: GitPr | null) {
		selectedPr = pr;
		selectedPrDetail = null;
		selectedCommit = null;
		selectedFile = null;
		fileDiff = [];
		prChecks = [];
		showDiff = true;
		if (!pr) return;
		await loadPullRequestDetail(pr);
		if (prSubview === 'code') await loadPullRequestDiff(pr);
		if (prSubview === 'checks') await loadPullRequestChecks(pr);
	}

	async function startCreatePullRequest() {
		selectedPr = null;
		selectedPrDetail = null;
		selectedCommit = null;
		selectedFile = null;
		fileDiff = [];
		prChecks = [];
		if (!branchData) await loadBranches();
		seedCreatePullRequest();
		showDiff = true;
	}

	function setPrState(state: PrState) {
		if (prState === state) return;
		prState = state;
		prLimit = 30;
		selectedPr = null;
		selectedPrDetail = null;
		fileDiff = [];
		prChecks = [];
		loadPullRequests();
	}

	let prSearchTimer: ReturnType<typeof setTimeout> | null = null;
	function onPrSearchInput() {
		if (prSearchTimer) clearTimeout(prSearchTimer);
		prSearchTimer = setTimeout(() => {
			prLimit = 30;
			loadPullRequests();
		}, 250);
	}

	function loadMorePullRequests() {
		prLimit += 30;
		loadPullRequests();
	}

	function setPrSubview(subview: PrSubview) {
		prSubview = subview;
		if (!selectedPr) return;
		if (subview === 'code' && !fileDiff.length) loadPullRequestDiff(selectedPr);
		if (subview === 'checks') loadPullRequestChecks(selectedPr);
		if (subview === 'summary' || subview === 'activity') loadPullRequestDetail(selectedPr);
	}

	async function loadPullRequestDetail(pr: GitPr) {
		prDetailLoading = true;
		try {
			selectedPrDetail = await getGitPrView(workspacePath, pr.number);
			prEditBase = selectedPrDetail.baseRefName || '';
		} catch {
			selectedPrDetail = pr;
			prEditBase = pr.baseRefName || '';
		} finally {
			prDetailLoading = false;
		}
	}

	async function loadPullRequestChecks(pr: GitPr) {
		try {
			const result = await getGitPrChecks(workspacePath, pr.number);
			prChecks = result.items ?? [];
		} catch (e) {
			prChecks = [];
			flash(e instanceof Error ? e.message : 'Could not load checks');
		}
	}

	async function submitPrReview(kind: 'comment' | 'approve' | 'request_changes') {
		if (!selectedPr) return;
		const body = prReviewBody.trim();
		if (kind === 'comment' && !body) return;
		try {
			if (kind === 'comment') await gitPrComment(workspacePath, selectedPr.number, body);
			else await gitPrReview(workspacePath, selectedPr.number, kind, body);
			prReviewBody = '';
			flash('Sent to GitHub');
			await loadPullRequests();
			await loadPullRequestDetail(selectedPr);
		} catch (e) {
			flash(e instanceof Error ? e.message : 'GitHub CLI action failed');
		}
	}

	async function createPullRequest() {
		if (!prCreateTitle.trim() || !prCreateBase.trim() || !prCreateHead.trim() || needsPublish)
			return;
		loading = true;
		try {
			const result = await gitPrCreate(workspacePath, {
				title: prCreateTitle.trim(),
				body: prCreateBody.trim(),
				base: prCreateBase.trim(),
				head: prCreateHead.trim() || gitStatus?.branch,
				draft: prCreateDraft
			});
			flash(result.url ? 'Pull request created' : 'Created pull request');
			prCreateTitle = '';
			prCreateBody = '';
			prCreateHead = '';
			await loadPullRequests();
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Could not create pull request');
		} finally {
			loading = false;
		}
	}

	function seedCreatePullRequest() {
		prCreateTitle = prCreateTitle || defaultPrTitle();
		prCreateHead = prCreateHead || gitStatus?.branch || '';
		if (!prCreateBase) {
			const upstream = gitStatus?.upstream || '';
			const upstreamBranch = upstream.includes('/') ? upstream.split('/').slice(1).join('/') : upstream;
			if (upstreamBranch && upstreamBranch !== gitStatus?.branch) {
				prCreateBase = upstreamBranch;
			} else if (prCapabilities?.default_branch) {
				prCreateBase = prCapabilities.default_branch;
			}
		}
	}

	function branchOptions(selected: string, includeDefault: boolean): { value: string; label: string }[] {
		const options: { value: string; label: string }[] = [];
		const seen = new Set<string>();
		const add = (value: string, label = value) => {
			const cleaned = value.trim();
			if (!cleaned || seen.has(cleaned)) return;
			seen.add(cleaned);
			options.push({ value: cleaned, label });
		};

		if (includeDefault && prCapabilities?.default_branch) add(prCapabilities.default_branch);
		for (const name of branchData?.local ?? []) add(name);
		for (const remote of branchData?.remote ?? []) {
			const short = remote.split('/').slice(1).join('/');
			if (!seen.has(short)) add(remote, short ? `${short} (${remote})` : remote);
		}
		if (selected && !seen.has(selected)) add(selected);
		return options;
	}

	function csvList(value: string): string[] {
		return value
			.split(',')
			.map((item) => item.trim())
			.filter(Boolean);
	}

	function signedCsvList(value: string): { add: string[]; remove: string[] } {
		const add: string[] = [];
		const remove: string[] = [];
		for (const item of csvList(value)) {
			if (item.startsWith('-') && item.slice(1).trim()) remove.push(item.slice(1).trim());
			else add.push(item.replace(/^\+/, '').trim());
		}
		return { add: add.filter(Boolean), remove };
	}

	async function updateSelectedPrReady(draft: boolean) {
		if (!selectedPr) return;
		loading = true;
		try {
			await gitPrReady(workspacePath, selectedPr.number, draft);
			flash(draft ? 'Converted to draft' : 'Marked ready for review');
			await loadPullRequests();
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Could not update pull request');
		} finally {
			loading = false;
		}
	}

	async function updateSelectedPrBranch(rebase = false) {
		if (!selectedPr) return;
		loading = true;
		try {
			await gitPrUpdateBranch(workspacePath, selectedPr.number, rebase);
			flash(rebase ? 'Rebased pull request branch' : 'Updated pull request branch');
			await loadPullRequests();
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Could not update branch');
		} finally {
			loading = false;
		}
	}

	async function closeSelectedPr() {
		if (!selectedPr) return;
		if (!confirm(`Close #${selectedPr.number}?`)) return;
		loading = true;
		try {
			await gitPrClose(workspacePath, selectedPr.number);
			flash('Closed pull request');
			selectedPr = null;
			selectedPrDetail = null;
			await loadPullRequests();
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Could not close pull request');
		} finally {
			loading = false;
		}
	}

	async function reopenSelectedPr() {
		if (!selectedPr) return;
		loading = true;
		try {
			await gitPrReopen(workspacePath, selectedPr.number);
			flash('Reopened pull request');
			await loadPullRequests();
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Could not reopen pull request');
		} finally {
			loading = false;
		}
	}

	async function mergeSelectedPr(strategy: 'merge' | 'squash' | 'rebase') {
		if (!selectedPr) return;
		if (!confirm(`Merge #${selectedPr.number} with ${strategy}?`)) return;
		loading = true;
		try {
			await gitPrMerge(workspacePath, selectedPr.number, {
				strategy,
				delete_branch: true
			});
			flash('Merged pull request');
			await loadPullRequests();
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Could not merge pull request');
		} finally {
			loading = false;
		}
	}

	async function editSelectedPrTitleBody() {
		if (!selectedPr) return;
		const detail = selectedPrDetail ?? selectedPr;
		const title = window.prompt('Title', detail.title)?.trim();
		if (!title) return;
		const body = window.prompt('Description', detail.body ?? '') ?? '';
		try {
			await gitPrEdit(workspacePath, selectedPr.number, { title, body });
			flash('Updated pull request');
			await loadPullRequests();
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Could not edit pull request');
		}
	}

	async function applySelectedPrMetadata() {
		if (!selectedPr) return;
		const reviewers = signedCsvList(prEditReviewers);
		const assignees = signedCsvList(prEditAssignees);
		const labels = signedCsvList(prEditLabels);
		const milestone = prEditMilestone.trim();
		const body = {
			base: prEditBase.trim() || undefined,
			add_reviewers: reviewers.add,
			remove_reviewers: reviewers.remove,
			add_assignees: assignees.add,
			remove_assignees: assignees.remove,
			add_labels: labels.add,
			remove_labels: labels.remove,
			milestone: milestone && milestone !== '-' ? milestone : undefined,
			remove_milestone: milestone === '-'
		};
		try {
			await gitPrEdit(workspacePath, selectedPr.number, body);
			prEditReviewers = '';
			prEditAssignees = '';
			prEditLabels = '';
			prEditMilestone = '';
			flash('Updated pull request');
			await loadPullRequests();
		} catch (e) {
			flash(e instanceof Error ? e.message : 'Could not edit pull request');
		}
	}

	function defaultPrTitle() {
		const branch = gitStatus?.branch || '';
		return branch ? branch.replace(/[-_/]+/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase()) : '';
	}

	function openGitSettings() {
		window.dispatchEvent(new CustomEvent('cptr:open-settings', { detail: { tab: 'git' } }));
	}

	function askCodexAboutSelectedPr() {
		if (!selectedPr || !workspacePath) return;
		const draft = [
			`Review this pull request and help me decide what to do next.`,
			``,
			`PR: #${selectedPr.number} ${selectedPr.title}`,
			`URL: ${selectedPr.url}`,
			`Branch: ${selectedPr.headRefName || '?'} -> ${selectedPr.baseRefName || '?'}`,
			`Files: ${selectedPr.changedFiles ?? 0}, +${selectedPr.additions ?? 0}/-${selectedPr.deletions ?? 0}`
		].join('\n');
		sessionStorage.setItem(`cptr:intent:chatDraft:${workspacePath}`, draft);
		openChatTab();
	}

	async function createBranch() {
		if (!newBranchName.trim()) return;
		const branch = newBranchName.trim();
		if (totalChanges > 0) {
			pendingCreateBranch = branch;
			creatingBranch = false;
			showBranches = false;
			return;
		}
		await performBranchCreate(branch, 'bring');
	}

	async function createWorktree() {
		if (!newWorktreeBranch.trim()) return;
		const branch = newWorktreeBranch.trim();
		loading = true;
		try {
			const result = (await createGitWorktree(workspacePath, branch)) as { path?: string };
			const target = result.path;
			newWorktreeBranch = '';
			creatingWorktree = false;
			if (target) {
				addWorkspace(target);
				showWorktrees = false;
				await goto(`/?workspace=${encodeURIComponent(target)}`);
			} else {
				await loadWorktrees();
			}
		} catch (e) {
			flash(e instanceof Error ? e.message : $t('git.createWorktreeFailed'));
		} finally {
			loading = false;
		}
	}

	async function performBranchCreate(branch: string, changeMode: 'bring' | 'leave') {
		loading = true;
		try {
			if (changeMode === 'leave') {
				const currentBranch = gitStatus?.branch || 'current branch';
				const stashResult = (await gitStash(
					workspacePath,
					`cptr-branch-switch:${currentBranch}`
				)) as {
					ok?: boolean;
					message?: string;
				};
				if (!stashResult.ok) {
					flash(stashResult.message || $t('git.noChangesStashed'));
					return;
				}
			}
			await createGitBranch(workspacePath, branch);
			newBranchName = '';
			creatingBranch = false;
			showBranches = false;
			pendingCreateBranch = null;
		} catch (e) {
			flash(e instanceof Error ? e.message : $t('git.createBranchFailed'));
		} finally {
			loading = false;
			await refresh();
		}
	}

	function flash(m: string) {
		actionMsg = m;
		setTimeout(() => {
			actionMsg = '';
		}, 3000);
	}

	// Context menu
	let contextMenu = $state<{ file: GitFile; anchor: HTMLElement } | null>(null);
	let commitMenu = $state<{ commit: Commit; isLatest: boolean; anchor: HTMLElement } | null>(null);

	function openFileMenu(e: MouseEvent, file: GitFile) {
		e.stopPropagation();
		contextMenu = { file, anchor: e.currentTarget as HTMLElement };
	}

	function closeContextMenu() {
		contextMenu = null;
	}

	function openCommitMenu(e: MouseEvent, commit: Commit, isLatest: boolean) {
		e.stopPropagation();
		commitMenu = { commit, isLatest, anchor: e.currentTarget as HTMLElement };
	}

	function closeCommitMenu() {
		commitMenu = null;
	}

	function closeBranchActionMenu() {
		branchActionMenu = null;
	}

	async function renameBranch(branch: BranchItem) {
		if (!branch.is_local) return;
		const nextName = window.prompt($t('git.renameBranch'), branch.name)?.trim();
		if (!nextName || nextName === branch.name) return;
		try {
			await renameGitBranch(workspacePath, branch.name, nextName);
			flash($t('git.branchRenamed'));
			closeBranchActionMenu();
			await refresh({ force: true });
			await loadBranches();
		} catch (e) {
			flash(e instanceof Error ? e.message : $t('git.renameBranchFailed'));
		}
	}

	function copyBranchName(branch: BranchItem) {
		navigator.clipboard.writeText(branch.name);
		flash($t('git.branchNameCopied'));
		closeBranchActionMenu();
	}

	async function deleteBranch(branch: BranchItem) {
		if (!branch.is_local || branch.is_current) return;
		if (!confirm($t('git.deleteBranchConfirm', { name: branch.name }))) return;
		try {
			await deleteGitBranch(workspacePath, branch.name);
			flash($t('git.branchDeleted'));
			closeBranchActionMenu();
			await refresh({ force: true });
			await loadBranches();
		} catch (e) {
			flash(e instanceof Error ? e.message : $t('git.deleteBranchFailed'));
		}
	}

	async function discardFile(path: string) {
		await discardChanges(workspacePath, [path]);
		if (selectedFile === path) {
			selectedFile = null;
			fileDiff = [];
		}
		flash($t('git.discarded'));
		await refresh();
	}

	function copyFilePath(path: string) {
		const full = workspacePath.endsWith('/') ? workspacePath + path : workspacePath + '/' + path;
		navigator.clipboard.writeText(full);
		flash($t('git.copiedPath'));
	}

	function copyRelativePath(path: string) {
		navigator.clipboard.writeText(path);
		flash($t('git.copiedRelativePath'));
	}

	// Path prefix in muted, filename in normal weight
	function fPath(p: string): { dir: string; name: string } {
		const i = p.lastIndexOf('/');
		return i >= 0 ? { dir: p.slice(0, i + 1), name: p.slice(i + 1) } : { dir: '', name: p };
	}

	function pathTail(path: string): string {
		return path.split(/[\\/]/).filter(Boolean).pop() || path;
	}

	function worktreeLabel(worktree: WorktreeItem): string {
		return worktree.branch || (worktree.is_detached ? 'Detached' : 'Worktree');
	}

	function relTime(d: string) {
		const s = Math.floor((Date.now() - new Date(d).getTime()) / 1000);
		if (s < 60) return 'now';
		if (s < 3600) return `${Math.floor(s / 60)}m`;
		if (s < 86400) return `${Math.floor(s / 3600)}h`;
		return `${Math.floor(s / 86400)}d`;
	}

	function prPersonName(person: unknown): string {
		if (!person || typeof person !== 'object') return '';
		const value = person as { login?: unknown; name?: unknown };
		return String(value.login || value.name || '').trim();
	}

	function prNames(items: unknown[] | undefined, limit = 3): string {
		const names = (items ?? []).map(prPersonName).filter(Boolean);
		if (!names.length) return '';
		const visible = names.slice(0, limit).join(', ');
		return names.length > limit ? `${visible} +${names.length - limit}` : visible;
	}

	function prLabelNames(labels: GitPr['labels'] | undefined): string {
		return (labels ?? [])
			.map((label) => label.name || '')
			.filter(Boolean)
			.join(', ');
	}

	function prReviewSignal(pr: GitPr | null): string {
		if (!pr) return '';
		if (pr.isDraft) return 'draft';
		if (pr.reviewDecision) return pr.reviewDecision.toLowerCase().replace(/_/g, ' ');
		if ((pr.reviewRequests ?? []).length) return 'review';
		return '';
	}

	function prCheckSignal(pr: GitPr | null): { label: string; tone: string } {
		const checks = (pr?.statusCheckRollup ?? []) as Array<{ conclusion?: string; status?: string }>;
		if (!checks.length) return { label: '', tone: 'text-gray-400 dark:text-gray-600' };
		const hasFail = checks.some((check) =>
			['FAILURE', 'TIMED_OUT', 'ACTION_REQUIRED', 'CANCELLED'].includes(
				String(check.conclusion || '').toUpperCase()
			)
		);
		if (hasFail) return { label: 'failing', tone: 'text-red-500 dark:text-red-400' };
		const hasPending = checks.some((check) =>
			['QUEUED', 'IN_PROGRESS', 'REQUESTED', 'WAITING', 'PENDING'].includes(
				String(check.status || check.conclusion || '').toUpperCase()
			)
		);
		if (hasPending) return { label: 'pending', tone: 'text-amber-500 dark:text-amber-400' };
		return { label: 'passing', tone: 'text-green-600 dark:text-green-400' };
	}

	function checkTone(check: GitPrCheck): string {
		switch (check.bucket) {
			case 'pass':
				return 'text-green-600 dark:text-green-400';
			case 'fail':
				return 'text-red-500 dark:text-red-400';
			case 'pending':
				return 'text-amber-500 dark:text-amber-400';
			default:
				return 'text-gray-400 dark:text-gray-600';
		}
	}

	function prBodyText(pr: GitPr | null): string {
		return (pr?.body ?? '').trim();
	}

	function prActivityItems(pr: GitPr | null): unknown[] {
		return [...((pr?.reviews ?? []) as unknown[]), ...((pr?.comments ?? []) as unknown[])];
	}

	function activityBody(item: unknown): string {
		if (!item || typeof item !== 'object') return '';
		const value = item as { body?: unknown; state?: unknown; author?: unknown; createdAt?: unknown };
		const author = prPersonName(value.author);
		const state = String(value.state || '').toLowerCase().replace(/_/g, ' ');
		const body = String(value.body || '').trim();
		return [author, state, body].filter(Boolean).join(' · ');
	}

	function statusChar(status: string): { char: string; color: string } {
		switch (status) {
			case 'added':
				return { char: 'A', color: 'text-green-500' };
			case 'untracked':
				return { char: 'U', color: 'text-green-500' };
			case 'modified':
				return { char: 'M', color: 'text-amber-500' };
			case 'deleted':
				return { char: 'D', color: 'text-red-400' };
			case 'renamed':
				return { char: 'R', color: 'text-blue-400' };
			case 'conflict':
				return { char: '!', color: 'text-orange-500' };
			default:
				return { char: '?', color: 'text-gray-400' };
		}
	}

	const syncAction = $derived.by(() => {
		if (!gitStatus) return { label: $t('git.fetch'), icon: 'refresh', action: doFetch };
		if (gitStatus.behind > 0)
			return {
				label: $t('git.pullCount', { count: gitStatus.behind }),
				icon: 'download',
				action: doPull
			};
		if (gitStatus.ahead > 0)
			return {
				label: $t('git.pushCount', { count: gitStatus.ahead }),
				icon: 'upload',
				action: doPush
			};
		if (!gitStatus.upstream) return { label: $t('git.publish'), icon: 'upload', action: doPush };
		return { label: $t('git.fetch'), icon: 'refresh', action: doFetch };
	});

	function onResizeStart(e: PointerEvent) {
		resizing = true;
		const startY = e.clientY;
		const startH = panelHeight;
		const maxH = getMaxHeight();
		function onMove(ev: PointerEvent) {
			panelHeight = Math.max(140, Math.min(maxH, startH - (ev.clientY - startY)));
		}
		function onUp() {
			resizing = false;
			window.removeEventListener('pointermove', onMove);
			window.removeEventListener('pointerup', onUp);
		}
		window.addEventListener('pointermove', onMove);
		window.addEventListener('pointerup', onUp);
	}

	function getMaxHeight() {
		return (containerEl?.parentElement?.clientHeight ?? window.innerHeight) - 100;
	}

	$effect(() => {
		if (!expanded) return;
		function onKeydown(e: KeyboardEvent) {
			if (!gitStatus?.is_repo) return;
			if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'p' && !e.shiftKey && !e.altKey) {
				e.preventDefault();
				doPush();
			}
		}
		window.addEventListener('keydown', onKeydown);
		return () => window.removeEventListener('keydown', onKeydown);
	});
</script>

{#if gitStatus?.is_repo}
	<div
		class="shrink-0 border-t border-gray-200 dark:border-white/6 relative"
		bind:this={containerEl}
	>
		<!-- Resize handle (top edge, styled like sidebar) -->
		{#if expanded}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="git-resize-handle" class:active={resizing} onpointerdown={onResizeStart}></div>
		{/if}

		<!-- Collapsed bar (entire bar is clickable to expand) -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="flex min-w-0 items-center h-7 px-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-white/3 transition-colors duration-75"
			onclick={() => {
				expanded = !expanded;
				if (expanded) {
					refresh();
					if (view === 'history') loadHistory();
					if (view === 'pullRequests') loadPullRequests();
				}
			}}
		>
			<!-- Branch button (opens branch picker, stops expand) -->
			<button
				bind:this={branchBtnEl}
				class="flex min-w-0 max-w-32 shrink items-center gap-1.5 h-6 px-1.5 -ml-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
				onclick={toggleBranches}
			>
				<Icon name="git-branch" size={12} class="text-gray-400 dark:text-gray-600 shrink-0" />
				<span
					class="min-w-0 truncate whitespace-nowrap text-[0.6875rem] text-gray-600 dark:text-gray-400 font-mono"
					>{gitStatus.branch}</span
				>
				<Icon name="chevron-down" size={9} class="text-gray-400 dark:text-gray-600" />
			</button>

			<button
				bind:this={worktreeBtnEl}
				class="flex min-w-0 max-w-32 shrink items-center gap-1.5 h-6 px-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
				onclick={toggleWorktrees}
			>
				<Icon name="folder" size={12} class="text-gray-400 dark:text-gray-600 shrink-0" />
				<span
					class="min-w-0 truncate whitespace-nowrap text-[0.6875rem] text-gray-500 dark:text-gray-500 font-mono"
					>{pathTail(workspacePath)}</span
				>
				<Icon name="chevron-down" size={9} class="text-gray-400 dark:text-gray-600" />
			</button>

			{#if gitStatus.ahead > 0}
				<span
					class="ml-1.5 shrink-0 whitespace-nowrap text-[0.625rem] font-mono text-gray-400 dark:text-gray-600"
				>
					↑{gitStatus.ahead}
				</span>
			{/if}
			{#if gitStatus.behind > 0}
				<span class="text-[0.625rem] font-mono text-gray-400 dark:text-gray-600 ml-1">
					↓{gitStatus.behind}
				</span>
			{/if}
			{#if totalChanges > 0}
				<span
					class="mx-1.5 block min-w-0 max-w-20 shrink truncate whitespace-nowrap text-[0.625rem] font-mono text-gray-400 dark:text-gray-600"
				>
					{$t('git.changedCount', { count: totalChanges })}
				</span>
				{#if totalAdditions || totalDeletions}
					<span class="mx-1.5 flex shrink-0 items-center gap-1 text-[0.625rem] font-mono">
						<span class="text-green-600 dark:text-green-400">+{totalAdditions}</span>
						<span class="text-red-500 dark:text-red-400">-{totalDeletions}</span>
					</span>
				{/if}
			{/if}
			{#if currentPr && prReady}
				<button
					class="ml-1 flex shrink-0 items-center gap-1 h-6 px-1.5 rounded-md text-[0.625rem] font-mono text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
					onclick={(e) => {
						e.stopPropagation();
						expanded = true;
						switchView('pullRequests');
						void selectPullRequest(currentPr);
					}}
				>
					<Icon name="git-diff" size={11} />
					<span>#{currentPr.number}</span>
				</button>
			{/if}
			{#if actionMsg}
				<span
					class="ml-auto min-w-0 max-w-32 truncate whitespace-nowrap text-[0.625rem] font-mono text-gray-400 dark:text-gray-600"
				>
					{actionMsg}
				</span>
			{/if}

			<div class="flex-1"></div>

			<!-- Sync button (stops expand) -->
			<button
				class="flex shrink-0 items-center gap-1 h-6 px-2 rounded-md text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
				onclick={(e) => {
					e.stopPropagation();
					syncAction.action();
				}}
				disabled={loading}
			>
				<Icon name={syncAction.icon} size={12} />
				<span class="whitespace-nowrap">{syncAction.label}</span>
			</button>

			<!-- Chevron indicator -->
			<span
				class="text-gray-300 dark:text-gray-700 shrink-0 transition-transform duration-100 ml-0.5"
				style="transform: rotate({expanded ? '180deg' : '0deg'})"
			>
				<Icon name="chevron-up" size={10} />
			</span>
		</div>

		<!-- Branch picker dropdown -->
		{#if showBranches && branchBtnEl}
			<DropdownMenu
				items={[]}
				anchor={branchBtnEl}
				onclose={() => {
					showBranches = false;
					creatingBranch = false;
					newBranchName = '';
				}}
				preferAbove
				forceAbove
				maxHeight="15rem"
				className="w-56"
				align="start"
				headerDivider={false}
				footerDivider={false}
			>
				{#snippet header()}
					<div class="px-2 pb-0.5">
						<div class="flex items-center gap-1.5 h-6 mt-0.5">
							<Icon name="search" size={13} class="shrink-0 text-gray-300 dark:text-gray-600" />
							<input
								bind:this={branchSearchInputEl}
								bind:value={branchSearch}
								placeholder={$t('search.search')}
								class="w-full bg-transparent text-[0.6875rem] text-gray-500 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-600 outline-none"
								onkeydown={(e) => {
									if (e.key === 'Escape') showBranches = false;
								}}
							/>
						</div>
						{#if !branchData}
							<div class="flex items-center justify-center h-10">
								<Spinner size={14} />
							</div>
						{/if}
					</div>
				{/snippet}
				{#snippet children()}
					{#if branchData}
						{#if filteredBranches.length > 0}
							<div
								class="px-2 pt-1 pb-0.5 text-[0.625rem] uppercase text-gray-300 dark:text-gray-600"
							>
								{$t('git.branches')}
							</div>
							{#each filteredBranches as branch (branch.name)}
								<div
									class="group flex items-center gap-1 w-full h-6 rounded-xl text-xs transition-colors duration-75
										{branch.is_current
										? 'text-gray-900 dark:text-white bg-gray-50 dark:bg-white/5'
										: 'text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white'}"
								>
									<button
										class="flex items-center gap-2 min-w-0 flex-1 h-full px-2 text-inherit"
										onclick={() => switchBranch(branch.name)}
									>
										<Icon name="git-branch" size={14} />
										<span class="flex-1 text-left truncate">{branch.name}</span>
										{#if branch.is_current}
											<svg
												class="w-3 h-3 shrink-0 text-gray-400 dark:text-gray-500"
												viewBox="0 0 24 24"
												fill="none"
												stroke="currentColor"
												stroke-width="2.5"
												stroke-linecap="round"
												stroke-linejoin="round"
											>
												<polyline points="20 6 9 17 4 12" />
											</svg>
										{/if}
									</button>
									<button
										class="flex items-center justify-center w-5 h-5 mr-0.5 rounded-lg shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200 dark:hover:bg-white/10 transition-all duration-75"
										aria-label={$t('git.branchActions')}
										onclick={(e) => {
											e.stopPropagation();
											branchActionMenu = { branch, anchor: e.currentTarget as HTMLElement };
										}}
									>
										<Icon name="three-dots" size={12} />
									</button>
								</div>
							{/each}
						{/if}

						{#if filteredBranches.length === 0}
							<div class="px-3 py-2 text-[0.6875rem] text-gray-400 dark:text-gray-500 text-center">
								{$t('git.noBranchesFound')}
							</div>
						{/if}
					{:else if branchData}
						<div class="px-3 py-2 text-[0.6875rem] text-gray-400 dark:text-gray-500 text-center">
							{$t('git.noBranchesFound')}
						</div>
					{/if}
				{/snippet}
				{#snippet footer()}
					{#if creatingBranch}
						<div class="flex items-center gap-2 h-7 px-2">
							<Icon name="git-branch" size={14} class="text-gray-400 shrink-0" />
							<input
								bind:this={newBranchInputEl}
								type="text"
								class="flex-1 border-none outline-none bg-transparent text-xs text-gray-900 dark:text-white placeholder:text-gray-400"
								placeholder={$t('git.branchName')}
								bind:value={newBranchName}
								onkeydown={(e) => {
									if (e.key === 'Enter') createBranch();
									if (e.key === 'Escape') {
										creatingBranch = false;
										newBranchName = '';
									}
								}}
							/>
							<button
								class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
								onclick={() => {
									creatingBranch = false;
									newBranchName = '';
								}}
							>
								<Icon name="xmark" size={12} />
							</button>
							<button
								class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
								onclick={createBranch}
							>
								<Icon name="check" size={12} />
							</button>
						</div>
					{:else}
						<button
							class="flex items-center gap-2 w-full h-7 px-2 rounded-xl text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white transition-colors"
							onclick={() => {
								creatingBranch = true;
								setTimeout(() => newBranchInputEl?.focus(), 0);
							}}
						>
							<Icon name="plus" size={14} />
							<span class="truncate">{$t('git.newBranch')}</span>
						</button>
					{/if}
				{/snippet}
			</DropdownMenu>
		{/if}

		<!-- Worktree picker dropdown -->
		{#if showWorktrees && worktreeBtnEl}
			<DropdownMenu
				items={[]}
				anchor={worktreeBtnEl}
				onclose={() => {
					showWorktrees = false;
					creatingWorktree = false;
					newWorktreeBranch = '';
				}}
				preferAbove
				forceAbove
				maxHeight="15rem"
				className="w-60"
				align="start"
				headerDivider={false}
				footerDivider={false}
			>
				{#snippet header()}
					<div class="px-2 pb-0.5">
						<div class="flex items-center gap-1.5 h-6 mt-0.5">
							<Icon name="search" size={13} class="shrink-0 text-gray-300 dark:text-gray-600" />
							<input
								bind:this={worktreeSearchInputEl}
								bind:value={worktreeSearch}
								placeholder={$t('git.searchWorktrees')}
								class="w-full bg-transparent text-[0.6875rem] text-gray-500 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-600 outline-none"
								onkeydown={(e) => {
									if (e.key === 'Escape') showWorktrees = false;
								}}
							/>
						</div>
						{#if !worktreeData}
							<div class="flex items-center justify-center h-10">
								<Spinner size={14} />
							</div>
						{/if}
					</div>
				{/snippet}
				{#snippet children()}
					{#if worktreeData}
						{#if filteredWorktrees.length > 0}
							<div
								class="px-2 pt-1 pb-0.5 text-[0.625rem] uppercase text-gray-300 dark:text-gray-600"
							>
								{$t('git.worktrees')}
							</div>
							{#each filteredWorktrees as worktree (worktree.path)}
								<button
									type="button"
									class="group flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs transition-colors duration-75
										{worktree.is_current
										? 'text-gray-900 dark:text-white bg-gray-50 dark:bg-white/5'
										: 'text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white'}"
									onclick={() => switchWorktree(worktree)}
								>
									<Icon name="folder" size={14} class="shrink-0" />
									<span class="min-w-0 flex-1 text-left truncate">{worktreeLabel(worktree)}</span>
									<span
										class="min-w-0 max-w-[45%] truncate text-[0.6875rem] text-gray-300 dark:text-gray-600"
									>
										{pathTail(worktree.path)}
									</span>
									{#if worktree.is_current}
										<svg
											class="w-3 h-3 shrink-0 text-gray-400 dark:text-gray-500"
											viewBox="0 0 24 24"
											fill="none"
											stroke="currentColor"
											stroke-width="2.5"
											stroke-linecap="round"
											stroke-linejoin="round"
										>
											<polyline points="20 6 9 17 4 12" />
										</svg>
									{/if}
								</button>
							{/each}
						{:else}
							<div class="px-3 py-2 text-[0.6875rem] text-gray-400 dark:text-gray-500 text-center">
								{$t('git.noWorktreesFound')}
							</div>
						{/if}
					{/if}
				{/snippet}
				{#snippet footer()}
					{#if creatingWorktree}
						<div class="flex items-center gap-2 h-7 px-2">
							<Icon name="folder" size={14} class="text-gray-400 shrink-0" />
							<input
								bind:this={newWorktreeInputEl}
								type="text"
								class="flex-1 border-none outline-none bg-transparent text-xs text-gray-900 dark:text-white placeholder:text-gray-400"
								placeholder={$t('git.branchName')}
								bind:value={newWorktreeBranch}
								onkeydown={(e) => {
									if (e.key === 'Enter') createWorktree();
									if (e.key === 'Escape') {
										creatingWorktree = false;
										newWorktreeBranch = '';
									}
								}}
							/>
							<button
								class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
								onclick={() => {
									creatingWorktree = false;
									newWorktreeBranch = '';
								}}
							>
								<Icon name="xmark" size={12} />
							</button>
							<button
								class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
								onclick={createWorktree}
								disabled={loading}
							>
								<Icon name="check" size={12} />
							</button>
						</div>
					{:else}
						<button
							class="flex items-center gap-2 w-full h-7 px-2 rounded-xl text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white transition-colors"
							onclick={() => {
								creatingWorktree = true;
								setTimeout(() => newWorktreeInputEl?.focus(), 0);
							}}
						>
							<Icon name="plus" size={14} />
							<span class="truncate">{$t('git.newWorktree')}</span>
						</button>
					{/if}
				{/snippet}
			</DropdownMenu>
		{/if}

		{#if expanded}
			<div class="border-t border-gray-100 dark:border-white/4">
				<!-- Header tabs -->
				<div
					class="flex items-center gap-0.5 h-8 px-2 border-b border-gray-100 dark:border-white/4"
				>
					<!-- Mobile back button when viewing diff -->
					{#if showDiff}
						<button
							class="flex md:hidden items-center gap-1 h-6 px-1.5 rounded-md text-[0.6875rem] text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
							onclick={backToList}
						>
							<Icon name="chevron-left" size={12} />
						</button>
					{/if}

					<!-- Tabs (always visible) -->
					<button
						class="px-2.5 h-6 rounded-md text-[0.6875rem] font-medium transition-colors duration-75
							{view === 'changes'
							? 'bg-gray-200/50 dark:bg-white/8 text-gray-900 dark:text-white'
							: 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'}"
						onclick={() => switchView('changes')}
						>Changes{#if totalChanges > 0}<span class="ml-1 text-gray-400 dark:text-gray-500"
								>{totalChanges}</span
							>{/if}</button
					>
					<button
						class="px-2.5 h-6 rounded-md text-[0.6875rem] font-medium transition-colors duration-75
								{view === 'history'
							? 'bg-gray-200/50 dark:bg-white/8 text-gray-900 dark:text-white'
							: 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'}"
						onclick={() => switchView('history')}>{$t('git.history')}</button
					>
					{#if prTabVisible}
						<button
							class="px-2.5 h-6 rounded-md text-[0.6875rem] font-medium transition-colors duration-75
									{view === 'pullRequests'
								? 'bg-gray-200/50 dark:bg-white/8 text-gray-900 dark:text-white'
								: 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'}"
							onclick={() => switchView('pullRequests')}
							>Pull requests{#if prReady && prCount > 0}<span
									class="ml-1 text-gray-400 dark:text-gray-500">{prCount}</span
								>{/if}</button
						>
					{/if}

					<div class="flex-1"></div>

					<!-- View on remote -->
					{#if remoteWebUrl}
						<a
							href={remoteWebUrl}
							target="_blank"
							rel="noopener noreferrer"
							class="flex items-center justify-center w-6 h-6 rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
							use:tooltip={'View on ' + remotePlatform}
						>
							<Icon name="external-link" size={12} />
						</a>
					{/if}

					<button
						bind:this={diffSettingsBtnEl}
						class="flex items-center justify-center w-6 h-6 rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
						onclick={() => (showDiffSettings = true)}
						use:tooltip={$t('diff.settings')}
						aria-label={$t('diff.settings')}
					>
						<Icon name="settings" size={12} />
					</button>

				</div>

				{#if showDiffSettings && diffSettingsBtnEl}
					<DiffSettingsMenu
						anchor={diffSettingsBtnEl}
						onclose={() => (showDiffSettings = false)}
						preferAbove
					/>
				{/if}

				<!-- Content -->
				<div class="flex min-h-36 min-w-0" style="height: {panelHeight}px;">
					<!-- Left pane: file list or commit list -->
					<div
						class="w-full min-w-0 md:w-60 lg:w-72 shrink-0 md:border-r md:border-gray-100 md:dark:border-white/4 flex flex-col overflow-hidden
						{showDiff ? 'hidden md:flex' : 'flex'}"
					>
						{#if view === 'changes'}
							<!-- Global checkbox -->
							{#if totalChanges > 0}
								<button
									class="flex min-w-0 items-center gap-1.5 h-7 px-2.5 border-b border-gray-100 dark:border-white/4 hover:bg-gray-50 dark:hover:bg-white/3 transition-colors duration-75 shrink-0"
									onclick={toggleAll}
								>
									<span
										class="flex items-center justify-center w-3 h-3 rounded border shrink-0
										{allStaged
											? 'border-gray-300 dark:border-gray-600 bg-gray-800 dark:bg-white'
											: someStaged
												? 'border-gray-300 dark:border-gray-600 bg-gray-400 dark:bg-gray-500'
												: 'border-gray-300 dark:border-gray-600'}"
									>
										{#if allStaged || someStaged}
											<Icon
												name={allStaged ? 'check' : 'minus'}
												size={7}
												class="text-white dark:text-black"
											/>
										{/if}
									</span>
									<span class="min-w-0 truncate text-[0.6875rem] text-gray-600 dark:text-gray-400"
										>{$t('git.changedFile', { count: totalChanges })}</span
									>
								</button>
							{/if}

							<!-- File list -->
							<div class="min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
								{#each gitStatus?.files ?? [] as file (file.path)}
									{@const fp = fPath(file.path)}
									{@const sc = statusChar(file.status)}
									<button
										class="group flex min-w-0 items-center gap-1.5 w-full h-7 px-2.5 text-left transition-colors duration-75
											{selectedFile === file.path
											? 'bg-gray-100 dark:bg-white/8'
											: 'hover:bg-gray-50 dark:hover:bg-white/3'}"
										onclick={() => selectFile(file.path, file.staged, file.status === 'untracked')}
									>
										<!-- svelte-ignore a11y_no_static_element_interactions -->
										<span
											class="flex items-center justify-center w-3 h-3 rounded border shrink-0 cursor-pointer
												{file.staged
												? 'border-gray-300 dark:border-gray-600 bg-gray-800 dark:bg-white'
												: 'border-gray-300 dark:border-gray-600 hover:border-gray-500 dark:hover:border-gray-400'}"
											onclick={(e) => {
												e.stopPropagation();
												toggleStage(e, file);
											}}
										>
											{#if file.staged}
												<Icon name="check" size={7} class="text-white dark:text-black" />
											{/if}
										</span>
										<span class="flex min-w-0 flex-1 items-baseline gap-1.5">
											{#if fp.dir}
												<span
													class="min-w-0 truncate text-[0.625rem] text-gray-400 dark:text-gray-600"
													>{fp.dir}</span
												>
											{/if}
											<!-- svelte-ignore a11y_no_static_element_interactions -->
											<span
												class="min-w-0 max-w-[55%] shrink-0 truncate text-[0.6875rem] text-gray-800 dark:text-gray-200 hover:underline cursor-pointer"
												onclick={(e) => {
													e.stopPropagation();
													openFileTab(workspacePath.replace(/\/$/, '') + '/' + file.path);
												}}>{fp.name}</span
											>
										</span>
										{#if file.binary}
											<span class="shrink-0 text-[0.625rem] font-mono font-bold {sc.color}"
												>{sc.char}</span
											>
										{:else}
											<span
												class="flex shrink-0 items-center gap-1 text-[0.625rem] font-mono font-medium"
											>
												<span class="text-green-600 dark:text-green-400"
													>+{file.additions ?? 0}</span
												>
												<span class="text-red-500 dark:text-red-400">-{file.deletions ?? 0}</span>
											</span>
										{/if}
										<!-- svelte-ignore a11y_no_static_element_interactions -->
										<span
											class="flex items-center justify-center w-5 h-5 rounded shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200 dark:hover:bg-white/10 transition-all duration-75"
											role="button"
											tabindex="-1"
											onclick={(e) => openFileMenu(e, file)}
											aria-label={$t('files.moreActions')}
										>
											<Icon name="three-dots" size={12} />
										</span>
									</button>
								{/each}
							</div>

							<!-- Commit area: combined input group -->
							<div class="border-t border-gray-100 dark:border-white/4 p-2 shrink-0">
								<div
									class="rounded-lg border border-gray-200 dark:border-white/8 overflow-hidden focus-within:border-gray-300 dark:focus-within:border-white/15 transition-colors"
								>
									<div class="relative">
										<input
											type="text"
											class="w-full h-7 pl-2 pr-7 bg-transparent text-[0.6875rem] text-gray-900 dark:text-white placeholder:text-gray-400 dark:placeholder:text-gray-600 outline-none"
											placeholder={$t('git.summaryRequired')}
											bind:value={commitSummary}
											onkeydown={(e) => {
												if (e.key === 'Enter' && !e.shiftKey) doCommit();
											}}
										/>
										<button
											class="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:cursor-default disabled:opacity-50 dark:text-gray-600 dark:hover:bg-white/10 dark:hover:text-gray-300"
											type="button"
											disabled={!stagedFiles.length || generatingCommitMessage}
											use:tooltip={$t('git.generateMessage')}
											aria-label={$t('git.generateMessage')}
											onclick={generateCommitMessage}
										>
											{#if generatingCommitMessage}
												<Spinner size={10} />
											{:else}
												<Icon name="spark" size={11} />
											{/if}
										</button>
									</div>
									<textarea
										class="w-full px-2 py-1.5 bg-transparent text-[0.6875rem] text-gray-900 dark:text-white placeholder:text-gray-400 dark:placeholder:text-gray-600 outline-none resize-none border-t border-gray-100 dark:border-white/4"
										rows="2"
										placeholder={$t('git.description')}
										bind:value={commitDescription}
									></textarea>
								</div>
								<button
									class="w-full h-7 mt-1.5 rounded-lg text-[0.6875rem] font-medium transition-colors duration-75
										{stagedFiles.length && commitSummary.trim()
										? 'bg-gray-900 dark:bg-white text-white dark:text-black hover:bg-gray-800 dark:hover:bg-white/90'
										: 'bg-gray-100 dark:bg-white/6 text-gray-400 dark:text-gray-600 cursor-default'}"
									disabled={!commitSummary.trim() || !stagedFiles.length || loading}
									onclick={doCommit}
								>
									{#if stagedFiles.length}
										{$t('git.commitFilesToBranch', {
											count: stagedFiles.length,
											branch: gitStatus.branch
										})}
									{:else}
										{$t('git.commitToBranch', { branch: gitStatus.branch })}
									{/if}
								</button>
							</div>
						{:else if view === 'history'}
							<!-- History: commit list -->
							<div class="min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
								{#each commits as c, i}
									{#if i === unpushedCount && unpushedCount > 0}
										<div
											class="flex items-center gap-2 px-2.5 py-1 border-b border-gray-100 dark:border-white/4 bg-gray-50/50 dark:bg-white/2"
										>
											<Icon
												name="upload"
												size={10}
												class="text-gray-400 dark:text-gray-600 shrink-0"
											/>
											<span class="text-[0.625rem] text-gray-400 dark:text-gray-600">
												{$t('git.unpushedCommit', { count: unpushedCount })}
											</span>
										</div>
									{/if}
									<button
										class="group flex items-center gap-1.5 w-full px-2.5 py-1.5 text-left border-b border-gray-50 dark:border-white/3 transition-colors duration-75
											{selectedCommit?.hash === c.hash
											? 'bg-gray-100 dark:bg-white/8'
											: 'hover:bg-gray-50 dark:hover:bg-white/3'}"
										onclick={() => selectCommit(c)}
									>
										{#if i < unpushedCount}
											<span
												class="w-1.5 h-1.5 rounded-full bg-blue-500 dark:bg-blue-400 shrink-0"
												title={$t('git.unpushed')}
											></span>
										{/if}
										<div class="flex flex-col min-w-0 flex-1">
											<span class="text-xs text-gray-800 dark:text-gray-200 truncate w-full"
												>{c.message}</span
											>
											<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 font-mono"
												>{c.short_hash} · {c.author} · {relTime(c.date)}</span
											>
										</div>
										<!-- svelte-ignore a11y_no_static_element_interactions -->
										<span
											class="flex items-center justify-center w-5 h-5 rounded shrink-0 opacity-0 group-hover:opacity-100 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200 dark:hover:bg-white/10 transition-all duration-75"
											role="button"
											tabindex="-1"
											onclick={(e) => openCommitMenu(e, c, i === 0)}
											aria-label={$t('files.moreActions')}
										>
											<Icon name="three-dots" size={12} />
										</span>
									</button>
								{/each}
								{#if !commits.length}
									<div
										class="flex items-center justify-center h-full text-[0.6875rem] text-gray-400 dark:text-gray-600"
									>
										{$t('git.noCommits')}
									</div>
								{/if}
							</div>
						{:else}
							<div class="flex min-w-0 flex-1 flex-col overflow-hidden">
								{#if !prCapabilities?.authenticated}
									<div class="border-b border-gray-100 px-2 py-2 dark:border-white/4">
										<div class="flex min-w-0 items-center gap-2">
											<Icon
												name="git-diff"
												size={13}
												class="shrink-0 text-gray-400 dark:text-gray-600"
											/>
											<div class="min-w-0 flex-1">
												<div class="truncate text-[0.6875rem] text-gray-700 dark:text-gray-300">
													GitHub CLI sign-in required
												</div>
												<div
													class="truncate font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
												>
													gh auth login
												</div>
											</div>
											<button
												class="h-6 shrink-0 rounded-md px-2 text-[0.6875rem] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-gray-200"
												onclick={openGitSettings}
											>
												Settings
											</button>
										</div>
									</div>
								{:else if prLoading}
									<div class="flex h-full items-center justify-center"><Spinner size={14} /></div>
								{:else if prError}
									<div
										class="px-3 py-8 text-center text-[0.6875rem] text-gray-400 dark:text-gray-600"
									>
										{prError}
									</div>
								{:else}
									<div class="border-b border-gray-100 p-2 dark:border-white/4">
											<div class="flex min-w-0 items-center gap-1">
												{#each prStates as state}
													<button
														class="h-6 rounded-md px-2 text-[0.6875rem] font-medium transition-colors duration-75
															{prState === state
															? 'bg-gray-200/50 text-gray-900 dark:bg-white/8 dark:text-white'
															: 'text-gray-400 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300'}"
														onclick={() => setPrState(state)}
													>
														{state[0].toUpperCase() + state.slice(1)}
													</button>
												{/each}
										</div>
										<div class="mt-1.5 flex min-w-0 items-center gap-1.5">
											<div
												class="flex h-6 min-w-0 flex-1 items-center gap-1.5 rounded-md border border-gray-200 px-1.5 dark:border-white/8"
											>
												<Icon name="search" size={11} class="shrink-0 text-gray-300 dark:text-gray-600" />
												<input
														class="min-w-0 flex-1 bg-transparent text-[0.6875rem] text-gray-700 outline-none placeholder:text-gray-300 dark:text-gray-300 dark:placeholder:text-gray-600"
														placeholder="Search pull requests"
													bind:value={prSearch}
													oninput={onPrSearchInput}
												/>
											</div>
											<button
												class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300"
												onclick={startCreatePullRequest}
												use:tooltip={'Create pull request'}
											>
												<Icon name="plus" size={12} />
											</button>
											</div>
										</div>
										<div class="min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
											{#each prs as pr (pr.number)}
											{@const checks = prCheckSignal(pr)}
											<button
												class="group flex w-full min-w-0 items-center gap-1.5 border-b border-gray-50 px-2.5 py-1.5 text-left transition-colors duration-75 dark:border-white/3
														{selectedPr?.number === pr.number
													? 'bg-gray-100 dark:bg-white/8'
													: 'hover:bg-gray-50 dark:hover:bg-white/3'}"
												onclick={() => selectPullRequest(pr)}
											>
												<Icon name="git-diff" size={12} class="shrink-0 text-gray-400 dark:text-gray-600" />
												<div class="flex min-w-0 flex-1 flex-col">
													<div class="flex min-w-0 items-center gap-1.5">
														<span class="min-w-0 flex-1 truncate text-xs text-gray-800 dark:text-gray-200"
															>#{pr.number} {pr.title}</span
														>
														{#if prReviewSignal(pr)}
															<span
																class="shrink-0 text-[0.625rem] text-gray-400 dark:text-gray-600"
																>{prReviewSignal(pr)}</span
															>
														{/if}
													</div>
													<div class="flex min-w-0 items-center gap-1.5 font-mono text-[0.625rem]">
														<span class="min-w-0 truncate text-gray-400 dark:text-gray-600"
															>{pr.headRefName} → {pr.baseRefName}</span
														>
														{#if checks.label}
															<span class="shrink-0 {checks.tone}">{checks.label}</span>
														{/if}
													</div>
												</div>
												<span class="shrink-0 font-mono text-[0.625rem] text-green-600 dark:text-green-400"
													>+{pr.additions ?? 0}</span
												>
												<span class="shrink-0 font-mono text-[0.625rem] text-red-500 dark:text-red-400"
													>-{pr.deletions ?? 0}</span
												>
											</button>
										{/each}
											{#if !prs.length}
												<div
													class="px-3 py-4 text-center text-[0.6875rem] text-gray-400 dark:text-gray-600"
												>
													No pull requests
												</div>
											{/if}
											{#if prs.length >= prLimit}
												<button
													class="h-7 w-full text-[0.6875rem] text-gray-400 transition-colors hover:bg-gray-50 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/3 dark:hover:text-gray-300"
													onclick={loadMorePullRequests}
												>
													Load more
												</button>
											{/if}
										</div>
								{/if}
							</div>
						{/if}
					</div>

					<!-- Right pane: diff viewer -->
					<div
						class="min-w-0 flex-1 overflow-hidden font-mono text-[0.6875rem] leading-[1.125rem]
							{showDiff ? 'flex flex-col' : 'hidden md:flex md:flex-col'}"
					>
						{#if view === 'pullRequests'}
							<div class="flex h-full min-w-0 flex-col overflow-hidden font-sans">
								{#if !prCapabilities?.authenticated}
									<div
										class="flex h-7 shrink-0 items-center gap-2 border-b border-gray-100 px-2 dark:border-white/4"
									>
										<span
											class="min-w-0 flex-1 truncate text-[0.625rem] font-mono text-gray-400 dark:text-gray-600"
											>gh auth</span
										>
									</div>
									<div class="flex h-full items-center justify-center px-6">
										<button
											class="h-7 rounded-md px-2.5 text-[0.6875rem] text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-gray-200"
											onclick={openGitSettings}
										>
											Open Git settings
										</button>
									</div>
								{:else if selectedPr}
									{@const detail = selectedPrInfo}
									<div class="flex h-7 shrink-0 items-center gap-2 border-b border-gray-100 px-2 dark:border-white/4">
										<span class="min-w-0 flex-1 truncate text-xs font-medium text-gray-800 dark:text-gray-200">
											#{selectedPr.number} {detail?.title ?? selectedPr.title}
										</span>
										<span class="font-mono text-[0.625rem] text-green-600 dark:text-green-400">+{detail?.additions ?? selectedPr.additions ?? 0}</span>
										<span class="font-mono text-[0.625rem] text-red-500 dark:text-red-400">-{detail?.deletions ?? selectedPr.deletions ?? 0}</span>
										<button
											class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300"
											onclick={editSelectedPrTitleBody}
											use:tooltip={'Edit'}
										>
											<Icon name="pencil" size={11} />
										</button>
										<button
											class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300"
											onclick={askCodexAboutSelectedPr}
											use:tooltip={'Draft chat'}
										>
											<Icon name="chat-fork" size={11} />
										</button>
										<a
											href={selectedPr.url}
											target="_blank"
											rel="noopener noreferrer"
											class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300"
											use:tooltip={'Open on GitHub'}
										>
											<Icon name="external-link" size={11} />
										</a>
									</div>
									<div class="flex h-7 shrink-0 items-center gap-1 border-b border-gray-50 px-2 dark:border-white/3">
										{#each prSubviews as subview}
											<button
												class="h-5 rounded px-1.5 text-[0.625rem] font-medium transition-colors
													{prSubview === subview
													? 'bg-gray-200/50 text-gray-900 dark:bg-white/8 dark:text-white'
													: 'text-gray-400 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300'}"
												onclick={() => setPrSubview(subview)}
											>
												{subview[0].toUpperCase() + subview.slice(1)}
											</button>
										{/each}
										<span class="ml-auto min-w-0 truncate font-mono text-[0.625rem] text-gray-400 dark:text-gray-600">
											{detail?.headRefName ?? selectedPr.headRefName} → {detail?.baseRefName ?? selectedPr.baseRefName}
										</span>
										<button
											class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300"
											onclick={() => checkoutPullRequest(selectedPr)}
											use:tooltip={'Checkout'}
										>
											<Icon name="git-branch" size={11} />
										</button>
									</div>
									{#if prSubview === 'summary'}
										<div class="min-h-0 flex-1 overflow-auto p-2 text-[0.6875rem] text-gray-600 dark:text-gray-400">
											{#if prDetailLoading}
												<div class="flex h-full items-center justify-center"><Spinner size={14} /></div>
											{:else}
												<div class="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-1">
													<span class="text-gray-400 dark:text-gray-600">Author</span>
													<span class="truncate">{prPersonName(detail?.author) || 'unknown'}</span>
													<span class="text-gray-400 dark:text-gray-600">Status</span>
													<span class="truncate">{detail?.isDraft ? 'Draft' : detail?.state ?? 'Open'} · {prReviewSignal(detail)}</span>
													<span class="text-gray-400 dark:text-gray-600">Reviewers</span>
													<span class="truncate">{prNames(detail?.reviewRequests as unknown[]) || 'none'}</span>
													<span class="text-gray-400 dark:text-gray-600">Assignees</span>
													<span class="truncate">{prNames(detail?.assignees as unknown[]) || 'none'}</span>
													<span class="text-gray-400 dark:text-gray-600">Labels</span>
													<span class="truncate">{prLabelNames(detail?.labels) || 'none'}</span>
													<span class="text-gray-400 dark:text-gray-600">Merge</span>
													<span class="truncate">{detail?.mergeStateStatus || detail?.mergeable || 'unknown'}</span>
												</div>
												<div class="mt-2 border-t border-gray-100 pt-2 dark:border-white/4">
													<div class="mb-1 text-[0.625rem] uppercase text-gray-300 dark:text-gray-600">Description</div>
													<div class="max-h-28 overflow-auto whitespace-pre-wrap text-xs leading-5 text-gray-800 dark:text-gray-200">
														{prBodyText(detail) || 'No description'}
													</div>
												</div>
													<div class="mt-2 flex flex-wrap gap-1 border-t border-gray-100 pt-2 dark:border-white/4">
														<button class="h-6 rounded-md px-2 text-[0.625rem] text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-white" onclick={() => updateSelectedPrBranch(false)}>Update</button>
														<button class="h-6 rounded-md px-2 text-[0.625rem] text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-white" onclick={() => updateSelectedPrReady(!detail?.isDraft)}>{detail?.isDraft ? 'Ready' : 'Draft'}</button>
														<button class="h-6 rounded-md px-2 text-[0.625rem] text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-white" onclick={() => mergeSelectedPr('squash')}>Squash</button>
														<button class="h-6 rounded-md px-2 text-[0.625rem] text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-white" onclick={() => mergeSelectedPr('merge')}>Merge</button>
														<button class="h-6 rounded-md px-2 text-[0.625rem] text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-white" onclick={() => mergeSelectedPr('rebase')}>Rebase</button>
														<button class="h-6 rounded-md px-2 text-[0.625rem] text-red-500 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10" onclick={detail?.state === 'CLOSED' ? reopenSelectedPr : closeSelectedPr}>{detail?.state === 'CLOSED' ? 'Reopen' : 'Close'}</button>
													</div>
													<div class="mt-2 grid grid-cols-2 gap-1 border-t border-gray-100 pt-2 dark:border-white/4">
														<input
															class="h-7 rounded-md border border-gray-200 bg-transparent px-2 text-[0.6875rem] text-gray-900 outline-none placeholder:text-gray-400 dark:border-white/8 dark:text-white dark:placeholder:text-gray-600"
															placeholder="Base"
															bind:value={prEditBase}
														/>
														<input
															class="h-7 rounded-md border border-gray-200 bg-transparent px-2 text-[0.6875rem] text-gray-900 outline-none placeholder:text-gray-400 dark:border-white/8 dark:text-white dark:placeholder:text-gray-600"
															placeholder="Reviewers (+name, -name)"
															bind:value={prEditReviewers}
														/>
														<input
															class="h-7 rounded-md border border-gray-200 bg-transparent px-2 text-[0.6875rem] text-gray-900 outline-none placeholder:text-gray-400 dark:border-white/8 dark:text-white dark:placeholder:text-gray-600"
															placeholder="Assignees (+name, -name)"
															bind:value={prEditAssignees}
														/>
														<input
															class="h-7 rounded-md border border-gray-200 bg-transparent px-2 text-[0.6875rem] text-gray-900 outline-none placeholder:text-gray-400 dark:border-white/8 dark:text-white dark:placeholder:text-gray-600"
															placeholder="Labels (+label, -label)"
															bind:value={prEditLabels}
														/>
														<input
															class="h-7 rounded-md border border-gray-200 bg-transparent px-2 text-[0.6875rem] text-gray-900 outline-none placeholder:text-gray-400 dark:border-white/8 dark:text-white dark:placeholder:text-gray-600"
															placeholder="Milestone (- to remove)"
															bind:value={prEditMilestone}
														/>
														<button
															class="h-7 rounded-md bg-gray-100 px-2 text-[0.6875rem] font-medium text-gray-600 transition-colors hover:bg-gray-200 hover:text-gray-900 dark:bg-white/6 dark:text-gray-300 dark:hover:bg-white/10 dark:hover:text-white"
															onclick={applySelectedPrMetadata}
														>
															Apply
														</button>
													</div>
													<div class="mt-2 overflow-hidden rounded-lg border border-gray-200 dark:border-white/8">
													<textarea
														class="h-16 w-full resize-none bg-transparent px-2 py-1.5 text-[0.6875rem] text-gray-900 outline-none placeholder:text-gray-400 dark:text-white dark:placeholder:text-gray-600"
														placeholder="Leave a review note"
														bind:value={prReviewBody}
													></textarea>
													<div class="flex items-center justify-end gap-1 border-t border-gray-100 p-1 dark:border-white/4">
														<button class="h-6 rounded px-2 text-[0.625rem] text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-white/6" onclick={() => submitPrReview('comment')}>Comment</button>
														<button class="h-6 rounded px-2 text-[0.625rem] text-green-600 hover:bg-green-50 dark:text-green-400 dark:hover:bg-green-500/10" onclick={() => submitPrReview('approve')}>Approve</button>
														<button class="h-6 rounded px-2 text-[0.625rem] text-red-500 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10" onclick={() => submitPrReview('request_changes')}>Request</button>
													</div>
												</div>
											{/if}
										</div>
									{:else if prSubview === 'code'}
										<div class="min-h-0 flex-1 overflow-auto font-mono text-[0.6875rem] leading-[1.125rem]">
											{#if prDiffLoading}
												<div class="flex h-full items-center justify-center"><Spinner size={14} /></div>
											{:else if fileDiff.length}
												<div class="diff-content" class:diff-content-split={$diffDisplayMode === 'split'}>
													{#each fileDiff as df}
														{#if fileDiff.length > 1}
															<div class="sticky top-0 z-10 border-b border-gray-100 px-2 py-1 text-[0.625rem] font-medium text-gray-500 dark:border-white/4 dark:text-gray-400" style="background: var(--app-bg); border-color: var(--app-border);">
																{df.path}
															</div>
														{/if}
														<DiffHunkList hunks={df.hunks} path={df.path} />
													{/each}
												</div>
											{:else}
												<div class="flex h-full items-center justify-center font-sans text-[0.6875rem] text-gray-400 dark:text-gray-600">No pull request changes</div>
											{/if}
										</div>
									{:else if prSubview === 'checks'}
										<div class="min-h-0 flex-1 overflow-auto">
											{#each prChecks as check, i (`${check.workflow || ''}-${check.name || i}`)}
												<a
													href={check.link}
													target="_blank"
													rel="noopener noreferrer"
													class="flex min-w-0 items-center gap-2 border-b border-gray-50 px-2.5 py-1.5 text-[0.6875rem] transition-colors hover:bg-gray-50 dark:border-white/3 dark:hover:bg-white/3"
												>
													<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-current {checkTone(check)}"></span>
													<span class="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-300">{check.name || check.workflow}</span>
													<span class="shrink-0 font-mono text-[0.625rem] {checkTone(check)}">{check.bucket || check.state}</span>
												</a>
											{/each}
											{#if !prChecks.length}
												<div class="flex h-full items-center justify-center text-[0.6875rem] text-gray-400 dark:text-gray-600">No checks</div>
											{/if}
										</div>
									{:else}
										<div class="min-h-0 flex-1 overflow-auto">
											{#each prActivityItems(detail) as item, i}
												<div class="border-b border-gray-50 px-2.5 py-1.5 text-[0.6875rem] text-gray-600 dark:border-white/3 dark:text-gray-400">
													{activityBody(item) || `Activity ${i + 1}`}
												</div>
											{/each}
											{#if !prActivityItems(detail).length}
												<div class="flex h-full items-center justify-center text-[0.6875rem] text-gray-400 dark:text-gray-600">No activity</div>
											{/if}
										</div>
									{/if}
									{:else}
										<div class="flex h-7 shrink-0 items-center gap-2 border-b border-gray-100 px-2 dark:border-white/4">
											<span class="min-w-0 flex-1 truncate text-xs font-medium text-gray-800 dark:text-gray-200">Create pull request</span>
											<span class="shrink-0 font-mono text-[0.625rem] text-gray-400 dark:text-gray-600">{gitStatus.branch}</span>
										</div>
											<div class="min-h-0 flex-1 overflow-auto p-2">
												<div class="overflow-hidden rounded-lg border border-gray-200 dark:border-white/8">
													<div class="grid h-7 grid-cols-[5.25rem_minmax(0,1fr)] items-center border-b border-gray-100 dark:border-white/4">
														<span class="px-2 text-[0.625rem] uppercase text-gray-300 dark:text-gray-600">Base</span>
														<select
															class="min-w-0 bg-transparent px-2 font-mono text-[0.6875rem] text-gray-900 outline-none dark:text-white"
															bind:value={prCreateBase}
														>
															{#each prBaseBranchOptions as branch}
																<option value={branch.value}>{branch.label}</option>
															{/each}
														</select>
													</div>
													<div class="grid h-7 grid-cols-[5.25rem_minmax(0,1fr)] items-center border-b border-gray-100 dark:border-white/4">
														<span class="px-2 text-[0.625rem] uppercase text-gray-300 dark:text-gray-600">Compare</span>
														<select
															class="min-w-0 bg-transparent px-2 font-mono text-[0.6875rem] text-gray-900 outline-none dark:text-white"
															bind:value={prCreateHead}
														>
															{#each prCompareBranchOptions as branch}
																<option value={branch.value}>{branch.label}</option>
															{/each}
														</select>
													</div>
													<input
														class="h-7 w-full border-b border-gray-100 bg-transparent px-2 text-[0.6875rem] text-gray-900 outline-none placeholder:text-gray-400 dark:border-white/4 dark:text-white dark:placeholder:text-gray-600"
														placeholder="Title"
														bind:value={prCreateTitle}
													/>
													<textarea
														class="h-20 w-full resize-none bg-transparent px-2 py-1.5 text-[0.6875rem] text-gray-900 outline-none placeholder:text-gray-400 dark:text-white dark:placeholder:text-gray-600"
														placeholder="Description"
														bind:value={prCreateBody}
													></textarea>
												</div>
												<div class="mt-1.5 flex min-w-0 items-center gap-1">
													<div class="flex h-6 shrink-0 overflow-hidden rounded-md border border-gray-200 dark:border-white/8">
														<button
															class="px-2 text-[0.625rem] transition-colors
																{!prCreateDraft
																? 'bg-gray-100 text-gray-800 dark:bg-white/8 dark:text-gray-200'
																: 'text-gray-400 hover:bg-gray-50 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/4 dark:hover:text-gray-300'}"
															onclick={() => (prCreateDraft = false)}
														>
															Ready
														</button>
														<button
															class="border-l border-gray-200 px-2 text-[0.625rem] transition-colors dark:border-white/8
																{prCreateDraft
																? 'bg-gray-100 text-gray-800 dark:bg-white/8 dark:text-gray-200'
																: 'text-gray-400 hover:bg-gray-50 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/4 dark:hover:text-gray-300'}"
															onclick={() => (prCreateDraft = true)}
														>
															Draft
														</button>
													</div>
												</div>
											{#if needsPublish}
												<div class="mt-1.5 flex items-center gap-2 rounded-md border border-gray-200 px-2 py-1 text-[0.625rem] text-gray-500 dark:border-white/8 dark:text-gray-400">
													<span class="min-w-0 flex-1 truncate">Publish branch before creating a pull request</span>
													<button class="h-6 rounded px-2 text-[0.625rem] text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-white/6 dark:hover:text-white" onclick={doPush}>Publish</button>
												</div>
											{/if}
												<button
													class="mt-1.5 h-7 rounded-lg bg-gray-900 px-3 text-[0.6875rem] font-medium text-white transition-colors hover:bg-gray-800 disabled:cursor-default disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-white/90"
													disabled={!prCreateTitle.trim() || !prCreateBase.trim() || !prCreateHead.trim() || needsPublish || loading}
													onclick={createPullRequest}
												>
													Create PR
												</button>
												<div class="mt-2 border-t border-gray-100 pt-2 dark:border-white/4">
													<div class="mb-1 flex h-5 min-w-0 items-center gap-2">
														<span class="min-w-0 flex-1 truncate text-[0.625rem] font-medium text-gray-400 dark:text-gray-600">
															Changes {prCreateBase || 'base'} → {prCreateHead || 'compare'}
														</span>
														{#if prCreateDiff.length}
															<span class="font-mono text-[0.625rem] text-green-600 dark:text-green-400">+{prCreateDiffStats.additions}</span>
															<span class="font-mono text-[0.625rem] text-red-500 dark:text-red-400">-{prCreateDiffStats.deletions}</span>
														{/if}
													</div>
													{#if prCreateDiffLoading}
														<div class="flex h-16 items-center justify-center"><Spinner size={14} /></div>
													{:else if prCreateDiffError}
														<div class="py-4 text-center text-[0.6875rem] text-gray-400 dark:text-gray-600">
															{prCreateDiffError}
														</div>
													{:else if prCreateDiff.length}
														<div class="overflow-hidden rounded-lg border border-gray-200 font-mono text-[0.6875rem] leading-[1.125rem] dark:border-white/8">
															<div class="diff-content" class:diff-content-split={$diffDisplayMode === 'split'}>
																{#each prCreateDiff as df}
																	{#if prCreateDiff.length > 1}
																		<div
																			class="sticky top-0 z-10 border-b border-gray-100 px-2 py-1 text-[0.625rem] font-medium text-gray-500 dark:border-white/4 dark:text-gray-400"
																			style="background: var(--app-bg); border-color: var(--app-border);"
																		>
																			{df.path}
																		</div>
																	{/if}
																	<DiffHunkList hunks={df.hunks} path={df.path} />
																{/each}
															</div>
														</div>
													{:else}
														<div class="py-4 text-center text-[0.6875rem] text-gray-400 dark:text-gray-600">
															No compare changes
														</div>
													{/if}
												</div>
										</div>
								{/if}
							</div>
						{:else if fileDiff.length}
							<div
								class="hidden md:flex items-center h-6 px-2 border-b border-gray-100 dark:border-white/4 shrink-0"
							>
								<span
									class="min-w-0 flex-1 text-[0.625rem] text-gray-400 dark:text-gray-600 font-mono truncate"
								>
									{#if selectedCommit}
										{selectedCommit.short_hash} · {selectedCommit.message}
									{:else}
										{selectedFile}
									{/if}
								</span>
								<span class="ml-2 text-[0.625rem] font-mono text-green-600 dark:text-green-400"
									>+{selectedDiffStats.additions}</span
								>
								<span class="text-[0.625rem] font-mono text-red-500 dark:text-red-400"
									>-{selectedDiffStats.deletions}</span
								>
							</div>
							<div class="flex-1 overflow-auto">
								<div class="diff-content" class:diff-content-split={$diffDisplayMode === 'split'}>
									{#each fileDiff as df}
										{#if fileDiff.length > 1}
											<div
												class="px-2 py-1 text-[0.625rem] text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-white/4 sticky top-0 z-10 font-medium"
												style="background: var(--app-bg); border-color: var(--app-border);"
											>
												{df.path}
											</div>
										{/if}
										<DiffHunkList hunks={df.hunks} path={df.path} />
									{/each}
								</div>
							</div>
						{:else}
							<div class="flex items-center justify-center h-full">
								{#if view === 'history' && !selectedCommit}
									<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
										>{$t('git.selectCommit')}</span
									>
								{:else if totalChanges}
									<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
										>{$t('git.selectFile')}</span
									>
								{:else}
									<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600 font-sans"
										>{$t('git.noLocalChanges')}</span
									>
								{/if}
							</div>
						{/if}
					</div>
				</div>
			</div>
		{/if}
	</div>
{/if}

{#if pendingCheckoutPr}
	<Modal onclose={() => (pendingCheckoutPr = null)} class="w-full max-w-sm mx-4">
		<div class="p-4">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white">Checkout pull request</h2>
			<p class="mt-1.5 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
				{$t('git.moveChangesPromptPrefix', { count: totalChanges })}
				<span class="font-mono text-gray-700 dark:text-gray-300">{gitStatus?.branch}</span>.
				{$t('git.moveChangesPromptMiddle')}
				<span class="font-mono text-gray-700 dark:text-gray-300">#{pendingCheckoutPr.number}</span>.
			</p>
			<div class="mt-4 flex items-center justify-end gap-2">
				<button
					class="px-2.5 py-1 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
					onclick={() => (pendingCheckoutPr = null)}
				>
					{$t('common.cancel')}
				</button>
				<button
					class="px-2.5 py-1 text-xs text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white transition-colors"
					onclick={() => performPrCheckout(pendingCheckoutPr!, 'leave')}
					disabled={loading}
				>
					{$t('git.leaveChanges')}
				</button>
				<button
					class="px-3.5 py-1.5 text-xs bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-white/90 transition-colors rounded-full disabled:opacity-50"
					onclick={() => performPrCheckout(pendingCheckoutPr!, 'bring')}
					disabled={loading}
				>
					{$t('git.bringChanges')}
				</button>
			</div>
		</div>
	</Modal>
{/if}

{#if pendingCheckoutBranch}
	<Modal onclose={() => (pendingCheckoutBranch = null)} class="w-full max-w-sm mx-4">
		<div class="p-4">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('git.switchBranches')}</h2>
			<p class="mt-1.5 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
				{$t('git.moveChangesPromptPrefix', { count: totalChanges })}
				<span class="font-mono text-gray-700 dark:text-gray-300">{gitStatus?.branch}</span>.
				{$t('git.moveChangesPromptMiddle')}
				<span class="font-mono text-gray-700 dark:text-gray-300">{pendingCheckoutBranch}</span>.
			</p>
			<div class="mt-4 flex items-center justify-end gap-2">
				<button
					class="px-2.5 py-1 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
					onclick={() => (pendingCheckoutBranch = null)}
				>
					{$t('common.cancel')}
				</button>
				<button
					class="px-2.5 py-1 text-xs text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white transition-colors"
					onclick={() => performBranchCheckout(pendingCheckoutBranch!, 'leave')}
					disabled={loading}
				>
					{$t('git.leaveChanges')}
				</button>
				<button
					class="px-3.5 py-1.5 text-xs bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-white/90 transition-colors rounded-full disabled:opacity-50"
					onclick={() => performBranchCheckout(pendingCheckoutBranch!, 'bring')}
					disabled={loading}
				>
					{$t('git.bringChanges')}
				</button>
			</div>
		</div>
	</Modal>
{/if}

{#if pendingCreateBranch}
	<Modal onclose={() => (pendingCreateBranch = null)} class="w-full max-w-sm mx-4">
		<div class="p-4">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white">
				{$t('git.createBranchPrompt')}
			</h2>
			<p class="mt-1.5 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
				{$t('git.moveChangesPromptPrefix', { count: totalChanges })}
				<span class="font-mono text-gray-700 dark:text-gray-300">{gitStatus?.branch}</span>.
				{$t('git.moveChangesPromptMiddle')}
				<span class="font-mono text-gray-700 dark:text-gray-300">{pendingCreateBranch}</span>.
			</p>
			<div class="mt-4 flex items-center justify-end gap-2">
				<button
					class="px-2.5 py-1 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
					onclick={() => (pendingCreateBranch = null)}
				>
					{$t('common.cancel')}
				</button>
				<button
					class="px-2.5 py-1 text-xs text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white transition-colors"
					onclick={() => performBranchCreate(pendingCreateBranch!, 'leave')}
					disabled={loading}
				>
					{$t('git.leaveChanges')}
				</button>
				<button
					class="px-3.5 py-1.5 text-xs bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-white/90 transition-colors rounded-full disabled:opacity-50"
					onclick={() => performBranchCreate(pendingCreateBranch!, 'bring')}
					disabled={loading}
				>
					{$t('git.bringChanges')}
				</button>
			</div>
		</div>
	</Modal>
{/if}

{#if branchActionMenu}
	<DropdownMenu
		anchor={branchActionMenu.anchor}
		items={[
			...(branchActionMenu.branch.is_local
				? [
						{
							label: $t('files.rename'),
							icon: 'pencil',
							onclick: () => renameBranch(branchActionMenu!.branch)
						}
					]
				: []),
			{
				label: $t('git.copyBranchName'),
				icon: 'copy',
				onclick: () => copyBranchName(branchActionMenu!.branch)
			},
			...(branchActionMenu.branch.is_local && !branchActionMenu.branch.is_current
				? [
						{
							label: $t('files.delete'),
							icon: 'xmark',
							onclick: () => deleteBranch(branchActionMenu!.branch)
						}
					]
				: [])
		]}
		onclose={closeBranchActionMenu}
	/>
{/if}

{#if contextMenu}
	<DropdownMenu
		anchor={contextMenu.anchor}
		items={[
			{
				label: $t('git.discard'),
				icon: 'xmark',
				onclick: () => discardFile(contextMenu!.file.path)
			},
			{
				label: $t('git.copyFilePath'),
				icon: 'copy',
				onclick: () => copyFilePath(contextMenu!.file.path)
			},
			{
				label: $t('git.copyRelativePath'),
				icon: 'copy',
				onclick: () => copyRelativePath(contextMenu!.file.path)
			}
		]}
		onclose={closeContextMenu}
	/>
{/if}

{#if commitMenu}
	<DropdownMenu
		anchor={commitMenu.anchor}
		items={[
			...(commitMenu.isLatest
				? [
						{
							label: $t('git.undoCommit'),
							icon: 'undo',
							onclick: () => {
								doUncommit();
								closeCommitMenu();
							}
						}
					]
				: []),
			{
				label: $t('git.copyCommitHash'),
				icon: 'copy',
				onclick: () => {
					navigator.clipboard.writeText(commitMenu!.commit.hash);
					flash($t('git.copiedPath'));
					closeCommitMenu();
				}
			}
		]}
		onclose={closeCommitMenu}
	/>
{/if}

<style>
	.diff-content {
		width: max-content;
		min-width: 100%;
	}

	.diff-content-split {
		width: 100%;
	}

	.git-resize-handle {
		position: absolute;
		top: -0.1875rem;
		left: 0;
		right: 0;
		height: 0.375rem;
		cursor: row-resize;
		z-index: 10;
		transition: background 0.15s;
	}

	.git-resize-handle:hover,
	.git-resize-handle.active {
		background: var(--app-active);
	}
</style>
