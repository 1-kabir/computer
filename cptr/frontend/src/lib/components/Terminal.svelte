<script lang="ts">
	import { onMount } from 'svelte';
	import { acquireTerminalSession, type TerminalSession } from '$lib/terminals';

	interface Props {
		sessionId?: string;
		wsPath?: string;
		initialOutput?: string;
		initialOffset?: number;
		stopSignal?: number;
		forceStopSignal?: number;
		readOnly?: boolean;
	}

	let {
		sessionId = '',
		wsPath = '',
		initialOutput = '',
		initialOffset = 0,
		stopSignal = 0,
		forceStopSignal = 0,
		readOnly = false
	}: Props = $props();

	let containerEl: HTMLDivElement | undefined = $state();
	let session: TerminalSession | null = null;

	// The xterm instance, scrollback, and WebSocket live in a module-level
	// cache keyed by session id (lib/terminals.ts). Mounting attaches the
	// cached terminal to this container; unmounting only DETACHES it — the
	// buffer and socket stay alive, so tab switches, workspace switches,
	// wide-screen breakpoint flips, and group changes on narrow screens
	// never destroy terminal history again. The session is only disposed
	// when its tab is closed (disposeTerminalSession) or the backend
	// reports the session is gone (WS close 4004).
	$effect(() => {
		session?.setReadOnly(readOnly);
	});

	let lastStopSignal = 0;
	$effect(() => {
		if (!stopSignal || stopSignal === lastStopSignal) return;
		lastStopSignal = stopSignal;
		session?.sendStop();
	});

	let lastForceStopSignal = 0;
	$effect(() => {
		if (!forceStopSignal || forceStopSignal === lastForceStopSignal) return;
		lastForceStopSignal = forceStopSignal;
		session?.sendForceStop();
	});

	onMount(() => {
		if (!containerEl) return;
		const key = sessionId || wsPath;
		if (!key) return;

		session = acquireTerminalSession(key, {
			wsPath,
			initialOutput,
			initialOffset,
			readOnly
		});
		session.attach(containerEl);

		return () => {
			// Detach only — do NOT dispose. The cached session keeps
			// running (and streaming) while this component is gone.
			session?.detach();
			session = null;
		};
	});
</script>

<div class="flex flex-col h-full w-full">
	<div bind:this={containerEl} class="flex-1 min-h-0 pt-1 pl-2 overflow-hidden"></div>
	<!-- The cached terminal's host div gets appended here on attach -->
</div>

<style>
	@reference "../../app.css";

	:global(.xterm) {
		height: 100%;
	}
	:global(.xterm-viewport) {
		overflow-y: auto !important;
		scrollbar-width: thin;
		scrollbar-color: color-mix(in oklab, var(--app-fg) 40%, transparent) transparent;
		overscroll-behavior: contain;
	}
</style>
