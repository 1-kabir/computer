<script lang="ts">
	import '../app.css';
	import '@xterm/xterm/css/xterm.css';

	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import ShortcutBar from '$lib/components/ShortcutBar.svelte';
	import GitBar from '$lib/components/GitBar.svelte';
	import SearchModal from '$lib/components/SearchModal.svelte';
	import SettingsModal from '$lib/components/SettingsModal.svelte';
	import AuthScreen from '$lib/components/AuthScreen.svelte';
	import ChangelogModal from '$lib/components/ChangelogModal.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import UpdateToast from '$lib/components/UpdateToast.svelte';
	import { Toaster, toast } from 'svelte-sonner';
	import {
		activeTab,
		activeHomeTab,
		currentWorkspace,
		stateLoaded,
		initState,
		gitReviewOpen,
		isGitRepo,
		splitActive,
		splitCurrentTab,
		closeGroup,
		appVersion,
		lastSeenVersion,
		latestVersion,
		updateAvailable,
		showChangelog,
		showSearch,
		showUpdateToastPref
	} from '$lib/stores';
	import { matchKeybinding, executeAction } from '$lib/stores/keybindings';
	import { systemEvents } from '$lib/stores/systemEvents.svelte';
	import { socketStore } from '$lib/stores/socket.svelte';
	import { setSession, clearSession, session } from '$lib/session';
	import { getSession, getConfig } from '$lib/apis/auth';
	import { fetchJSON } from '$lib/apis';
	import { gitStatusStore } from '$lib/stores/gitStatus.svelte';
	import { t } from '$lib/i18n';
	import { refreshChatState, bindGlobalChatListener } from '$lib/stores/chat';
	import { refreshAudioState } from '$lib/stores/audio';
	import SetupWizard from '$lib/components/SetupWizard.svelte';

	let { children } = $props();
	let showSettings = $state(false);
	let showUpdateToast = $state(false);
	let showSetup = $state(false);
	let connectionToast: string | number | undefined;
	let applyingServiceWorkerUpdate = false;
	let lastGitRefreshFsTick = 0;
	let gitRefreshTimer: ReturnType<typeof setTimeout> | null = null;
	const BROWSER_SW_CLEANUP_RELOAD = 'cptr:pwa:browser-sw-cleanup-reload';

	// Auth state
	type AuthState = 'checking' | 'needs_setup' | 'needs_login' | 'authenticated';
	let authState = $state<AuthState>('checking');
	let authMode = $state<'password' | 'pam'>('password');
	let signupEnabled = $state(false);

	onMount(async () => {
		// Check auth first
		await checkAuth();

		// Periodic session health check (every 30 min).
		// This triggers the backend's sliding session refresh and
		// proactively catches expired sessions before a 401 mid-action.
		const healthCheck = setInterval(
			() => {
				if (authState === 'authenticated') {
					getSession()
						.then((auth) => {
							if (!auth.authenticated) clearSession();
						})
						.catch(() => {});
				}
			},
			30 * 60 * 1000
		);
		// iOS Safari keeps 100vh/100dvh at the layout viewport height when
		// the keyboard opens. visualViewport tells us how much of the bottom
		// of that layout viewport is covered, so reserve that space inside the
		// main column instead of moving or clipping the whole shell.
		const vv = window.visualViewport;
		const syncKeyboardInset = () => {
			const visualBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
			const keyboardInset = Math.max(0, window.innerHeight - visualBottom);
			document.documentElement.style.setProperty(
				'--keyboard-inset-bottom',
				keyboardInset > 100 ? `${keyboardInset}px` : '0'
			);
		};

		syncKeyboardInset();
		window.addEventListener('resize', syncKeyboardInset);
		// iOS may fire 'scroll' instead of 'resize' when keyboard opens.
		vv?.addEventListener('resize', syncKeyboardInset);
		vv?.addEventListener('scroll', syncKeyboardInset);

		if (isInstalledPwa()) {
			registerServiceWorker().catch(() => {});
		} else {
			cleanBrowserServiceWorker().catch(() => {});
		}
		window.addEventListener('offline', showOfflineToast);
		window.addEventListener('online', showOnlineToast);

		return () => {
			clearInterval(healthCheck);
			document.documentElement.style.removeProperty('--keyboard-inset-bottom');
			window.removeEventListener('resize', syncKeyboardInset);
			vv?.removeEventListener('resize', syncKeyboardInset);
			vv?.removeEventListener('scroll', syncKeyboardInset);
			window.removeEventListener('offline', showOfflineToast);
			window.removeEventListener('online', showOnlineToast);
		};
	});

	let startupToken = $state('');

	$effect(() => {
		if ($stateLoaded && $appVersion) {
			const currentVer = $appVersion;
			const lastSeen = $lastSeenVersion;
			if (lastSeen !== currentVer) {
				if (!lastSeen) {
					// First-time load: initialize the last seen version to the current version so we don't pop up immediately
					lastSeenVersion.set(currentVer);
				} else {
					// Update: show the changelog until the user dismisses it.
					showChangelog.set(true);
				}
			}
		}
	});

	// Check ?setup=true param for admin setup wizard
	$effect(() => {
		if (!$stateLoaded) return;
		if (authState !== 'authenticated') return;

		const params = new URLSearchParams(window.location.search);
		if (params.get('setup') === 'true' && $session?.role === 'admin') {
			showSetup = true;
			const url = new URL(window.location.href);
			url.searchParams.delete('setup');
			window.history.replaceState({}, '', url.toString());
		}
	});

	async function checkAuth() {
		try {
			const params = new URLSearchParams(window.location.search);
			const token = params.get('token');
			if (token) {
				startupToken = token;
				// Remove token from URL but preserve workspace param
				const url = new URL(window.location.href);
				url.searchParams.delete('token');
				window.history.replaceState({}, '', url.toString());
			}

			const auth = await getSession();

			getConfig()
				.then((cfg) => {
					appVersion.set(cfg.version);
				})
				.catch(() => {});

			if (auth.authenticated) {
				setSession({
					user_id: auth.user_id!,
					username: auth.username!,
					display_name: auth.display_name,
					role: auth.role!,
					profile_image_url: auth.profile_image_url
				});
				authState = 'authenticated';
				initState();
				refreshChatState();
				refreshAudioState();

				// Check for version updates (admin only, after session is set)
				checkForUpdates();
			} else {
				const cfg = await getConfig();
				authMode = cfg.auth_mode || 'password';
				signupEnabled = cfg.signup_enabled || false;
				authState = cfg.needs_setup ? 'needs_setup' : 'needs_login';
			}
		} catch {
			authState = 'authenticated';
			initState();
			refreshChatState();
			refreshAudioState();
		}
	}

	async function handleAuth() {
		const wasSetup = authState === 'needs_setup';
		try {
			const auth = await getSession();
			if (auth.authenticated) {
				setSession({
					user_id: auth.user_id!,
					username: auth.username!,
					display_name: auth.display_name,
					role: auth.role!,
					profile_image_url: auth.profile_image_url
				});
				authState = 'authenticated';
				initState();
				refreshChatState();
				refreshAudioState();
				if (wasSetup) showSetup = true;
				return;
			}
		} catch {}
		authState = 'needs_login';
	}

	async function checkForUpdates() {
		try {
			const sess = $session;
			if (!sess || sess.role !== 'admin') return;
			if (!$showUpdateToastPref) return;

			// 24-hour dismiss cooldown
			const dismissed = localStorage.getItem('dismissedUpdateToast');
			if (dismissed) {
				const elapsed = Date.now() - Number(dismissed);
				if (elapsed < 24 * 60 * 60 * 1000) return;
			}

			const data = await fetchJSON<{ current: string; latest: string }>('/api/version/updates');
			latestVersion.set(data.latest);
			// Show toast if update is available (reactive via $updateAvailable)
			if (data.current !== data.latest) {
				showUpdateToast = true;
			}
		} catch {
			// Silently ignore (non-admin, network error, etc.)
		}
	}

	function isInstalledPwa() {
		const nav = navigator as Navigator & { standalone?: boolean };
		return (
			nav.standalone === true ||
			window.matchMedia('(display-mode: standalone)').matches ||
			window.matchMedia('(display-mode: window-controls-overlay)').matches
		);
	}

	function showOfflineToast() {
		if (connectionToast) return;
		connectionToast = toast.error($t('pwa.unreachable'), { duration: Infinity });
	}

	function showOnlineToast() {
		if (connectionToast) toast.dismiss(connectionToast);
		connectionToast = undefined;
		toast.success($t('pwa.connectionRestored'));
	}

	async function clearCptrCaches() {
		if (!('caches' in window)) return;
		const keys = await caches.keys();
		await Promise.all(
			keys.filter((key) => key.startsWith('cptr-')).map((key) => caches.delete(key))
		);
	}

	function isCptrWorker(registration: ServiceWorkerRegistration) {
		const script =
			registration.active?.scriptURL ||
			registration.waiting?.scriptURL ||
			registration.installing?.scriptURL ||
			'';
		return script.endsWith('/service-worker.js');
	}

	async function cleanBrowserServiceWorker() {
		if (!('serviceWorker' in navigator)) return;
		const registrations = await navigator.serviceWorker.getRegistrations();
		const cptrRegistrations = registrations.filter(isCptrWorker);
		if (!cptrRegistrations.length) {
			sessionStorage.removeItem(BROWSER_SW_CLEANUP_RELOAD);
			return;
		}

		const hadController = !!navigator.serviceWorker.controller;
		await Promise.all(cptrRegistrations.map((registration) => registration.unregister()));
		await clearCptrCaches();
		if (hadController && !sessionStorage.getItem(BROWSER_SW_CLEANUP_RELOAD)) {
			sessionStorage.setItem(BROWSER_SW_CLEANUP_RELOAD, '1');
			location.reload();
		}
	}

	async function registerServiceWorker() {
		if (!('serviceWorker' in navigator)) return;
		const registration = await navigator.serviceWorker.register('/service-worker.js');
		if (registration.waiting && navigator.serviceWorker.controller) {
			applyServiceWorkerUpdate(registration);
		}
		registration.addEventListener('updatefound', () => {
			const worker = registration.installing;
			if (!worker) return;
			worker.addEventListener('statechange', () => {
				if (worker.state === 'installed' && navigator.serviceWorker.controller) {
					applyServiceWorkerUpdate(registration);
				}
			});
		});
		navigator.serviceWorker.addEventListener('controllerchange', () => {
			if (applyingServiceWorkerUpdate) window.location.reload();
		});
	}

	function applyServiceWorkerUpdate(registration: ServiceWorkerRegistration) {
		applyingServiceWorkerUpdate = true;
		registration.waiting?.postMessage({ type: 'SKIP_WAITING' });
	}

	function handleKeydown(e: KeyboardEvent) {
		const action = matchKeybinding(e);
		if (!action) return;
		e.preventDefault();
		executeAction(action, {
			toggleQuickOpen: () => {
				showSearch.update((v) => !v);
			},
			toggleSettings: () => {
				showSettings = !showSettings;
			},
			toggleSearch: () => {
				showSearch.update((v) => !v);
			},
			toggleVoiceMemo: () => {
				import('$lib/stores/audio').then(({ voiceMemosEnabled, showVoiceMemo }) => {
					import('svelte/store').then(({ get }) => {
						if (get(voiceMemosEnabled)) showVoiceMemo.update((v) => !v);
					});
				});
			}
		});
	}

	// Chat events belong to the authenticated user, not a workspace.
	$effect(() => {
		if (authState === 'authenticated') {
			socketStore.connect();
			bindGlobalChatListener();
		} else {
			socketStore.disconnect();
		}
	});

	// Filesystem events remain workspace-scoped.
	$effect(() => {
		const ws = $currentWorkspace;
		if (ws) {
			systemEvents.connect(ws.fileBrowserCwd || ws.path);
		} else {
			systemEvents.disconnect();
		}
	});

	// Drive centralized git status from workspace
	$effect(() => {
		const ws = $currentWorkspace;
		if (!ws) {
			gitStatusStore.clear();
			isGitRepo.set(false);
			gitReviewOpen.set(false);
			return;
		}
		gitStatusStore.setRoot(ws.path);
	});

	// Keep git decorations fresh after filesystem changes.
	$effect(() => {
		const tick = systemEvents.fsTick;
		const ws = $currentWorkspace;
		if (tick === 0 || tick === lastGitRefreshFsTick || !ws) return;
		lastGitRefreshFsTick = tick;
		if (!systemEvents.isRelevantFsChange(ws.path)) return;

		if (gitRefreshTimer) clearTimeout(gitRefreshTimer);
		gitRefreshTimer = setTimeout(() => {
			gitRefreshTimer = null;
			gitStatusStore.refresh({ force: true });
		}, 250);
	});

	// Sync isGitRepo flag from centralized store
	$effect(() => {
		isGitRepo.set(gitStatusStore.isRepo);
	});
</script>

<svelte:head>
	<link rel="preconnect" href="https://fonts.googleapis.com" />
	<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous" />
	<link
		href="https://fonts.googleapis.com/css2?family=Inter:wght@300..700&family=JetBrains+Mono:wght@400;500&display=swap"
		rel="stylesheet"
	/>
	<title
		>{$activeTab && $activeTab.type !== 'files'
			? $currentWorkspace
				? `${$activeTab.label} / ${$currentWorkspace.name} / Computer`
				: `${$activeTab.label} / Computer`
			: $currentWorkspace
				? `${$currentWorkspace.name} / Computer`
				: 'Computer'}</title
	>
	<meta name="description" content={$t('app.tagline')} />
</svelte:head>

<svelte:window onkeydown={handleKeydown} />

{#if authState === 'checking'}
	<!-- Loading spinner while checking auth -->
	<div
		class="app-theme flex items-center justify-center h-dvh bg-white dark:bg-black"
		style="background: var(--app-bg); color: var(--app-fg);"
	>
		<Spinner size={20} />
	</div>
{:else if authState === 'needs_setup' || authState === 'needs_login'}
	<!-- Auth screen -->
	<AuthScreen
		mode={authMode}
		needsSetup={authState === 'needs_setup'}
		{signupEnabled}
		token={startupToken}
		onauth={handleAuth}
	/>
{:else if $stateLoaded && showSetup}
	<SetupWizard
		oncomplete={() => {
			showSetup = false;
		}}
	/>
{:else if $stateLoaded}
	<div
		class="app-theme h-screen max-h-[100dvh] flex overflow-hidden font-sans antialiased text-gray-900 bg-white dark:text-gray-100 dark:bg-black"
		style="background: var(--app-bg); color: var(--app-fg);"
	>
		<Sidebar />

		<div
			id="main-col"
			class="flex flex-col flex-1 min-w-0 min-h-0 overflow-hidden"
			style="padding-top: env(safe-area-inset-top, 0); padding-bottom: var(--keyboard-inset-bottom, 0);"
		>
			<main class="relative flex-1 min-h-0 overflow-hidden">
				{@render children()}
			</main>

			{#if $currentWorkspace && $isGitRepo && !$gitReviewOpen}
				<GitBar />
			{/if}

			{#if $activeTab?.type === 'terminal' || (!$currentWorkspace && $activeHomeTab?.type === 'terminal')}
				<ShortcutBar />
			{/if}
		</div>
	</div>

	{#if $showSearch}
		<SearchModal onclose={() => showSearch.set(false)} />
	{/if}
	{#if showSettings}
		<SettingsModal onclose={() => (showSettings = false)} />
	{/if}
	<ChangelogModal />
	{#if $updateAvailable && showUpdateToast}
		<UpdateToast
			onclose={() => {
				showUpdateToast = false;
				localStorage.setItem('dismissedUpdateToast', Date.now().toString());
			}}
		/>
	{/if}
{:else}
	<div
		class="app-theme flex items-center justify-center h-dvh bg-white dark:bg-black"
		style="background: var(--app-bg); color: var(--app-fg);"
	>
		<Spinner size={20} />
	</div>
{/if}

<Toaster
	position="top-right"
	theme="system"
	closeButton
	richColors
	toastOptions={{
		style: 'font-size: 0.75rem; font-family: var(--font-sans); border-radius: 0.5rem;'
	}}
/>
<ConfirmDialog />
