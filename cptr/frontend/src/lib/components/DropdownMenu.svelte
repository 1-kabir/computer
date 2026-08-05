<script lang="ts">
	import { onMount, tick, type Snippet } from 'svelte';
	import { tooltip } from '$lib/tooltip';
	import Icon from './Icon.svelte';
	import KeyPill from './KeyPill.svelte';

	interface MenuItem {
		label: string;
		tooltip?: string;
		icon?: string;
		onclick: () => void;
		active?: boolean;
		highlighted?: boolean;
		divider?: boolean;
		/** Optional image URL shown instead of icon (e.g. avatar). */
		image?: string;
		/** Optional check mark on the right when active. */
		check?: boolean;
		/** Optional keyboard shortcut hint displayed as a single pill on the right. */
		shortcut?: string;
		/** Optional trailing action button, e.g. row options. */
		actionIcon?: string;
		actionLabel?: string;
		actionOnclick?: (anchor: HTMLElement) => void;
	}

	interface Props {
		items: MenuItem[];
		anchor: { x: number; y: number } | HTMLElement;
		onclose: () => void;
		/** When true, match the anchor element's width. */
		matchWidth?: boolean;
		/** Prefer opening above the anchor instead of below. */
		preferAbove?: boolean;
		/** Keep the menu above the anchor and shrink it to fit the visible viewport. */
		forceAbove?: boolean;
		/** Position above the trigger in normal layout instead of fixed to the viewport. */
		inlineAbove?: boolean;
		/** Max height for the items list (CSS value). */
		maxHeight?: string;
		/** Optional header snippet rendered above items (e.g. search input). */
		header?: Snippet;
		/** Optional footer snippet rendered below items (e.g. pinned actions). */
		footer?: Snippet;
		/** Show a divider after the header. */
		headerDivider?: boolean;
		/** Show a divider before the footer. */
		footerDivider?: boolean;
		/** Optional snippet rendered when items array is empty. */
		empty?: Snippet;
		/** Optional custom dropdown content. */
		children?: Snippet;
		/** Additional CSS classes for the menu container. */
		className?: string;
		/** Horizontal alignment relative to anchor: 'start' (left) or 'end' (right). */
		align?: 'start' | 'end';
		/** Scroll the active or highlighted item into the list viewport after render. */
		scrollActiveIntoView?: boolean;
		scrollActiveBlock?: ScrollLogicalPosition;
	}

	let {
		items,
		anchor,
		onclose,
		matchWidth = false,
		preferAbove = false,
		forceAbove = false,
		inlineAbove = false,
		maxHeight,
		header,
		footer,
		headerDivider = true,
		footerDivider = true,
		empty,
		children,
		className = '',
		align = 'start',
		scrollActiveIntoView = false,
		scrollActiveBlock = 'nearest'
	}: Props = $props();

	let menuEl: HTMLDivElement | undefined = $state();
	let listEl: HTMLDivElement | undefined = $state();
	let pos = $state<{ x: number; top?: number; bottom?: number }>({ x: -9999, top: -9999 });
	let anchorWidth = $state(0);
	let menuMaxHeight = $state<number | undefined>();
	let ready = $state(false);
	let frame: number | undefined;
	let anchorFrame: number | undefined;
	let settleTimers: number[] = [];
	let lastViewportState = '';
	let lastAnchorState = '';

	function portal(node: HTMLElement, enabled = true) {
		const parent = node.parentNode;
		const sibling = node.nextSibling;

		function move() {
			if (enabled && node.parentNode !== document.body) {
				document.body.appendChild(node);
			} else if (!enabled && parent && node.parentNode === document.body) {
				parent.insertBefore(node, sibling);
			}
		}

		move();

		return {
			update(nextEnabled: boolean) {
				enabled = nextEnabled;
				move();
			},
			destroy() {
				node.remove();
			}
		};
	}

	function visualViewportRect() {
		const vv = window.visualViewport;
		return {
			left: vv?.offsetLeft ?? 0,
			top: vv?.offsetTop ?? 0,
			width: vv?.width ?? window.innerWidth,
			height: vv?.height ?? window.innerHeight
		};
	}

	function viewportState() {
		const viewport = visualViewportRect();
		return [
			viewport.left,
			viewport.top,
			viewport.width,
			viewport.height,
			window.innerWidth,
			window.innerHeight
		].join(':');
	}

	function anchorState() {
		if (!(anchor instanceof HTMLElement)) {
			return `${anchor.x}:${anchor.y}:${viewportState()}`;
		}

		const rect = anchor.getBoundingClientRect();
		return [rect.left, rect.top, rect.right, rect.bottom, rect.width, rect.height, viewportState()]
			.map((value) => (typeof value === 'number' ? value.toFixed(2) : value))
			.join(':');
	}

	function measureMenu() {
		if (!menuEl) return { width: 0, height: 0 };

		const previousMaxHeight = menuEl.style.maxHeight;
		menuEl.style.maxHeight = '';
		const size = {
			width: menuEl.offsetWidth,
			height: menuEl.offsetHeight
		};
		menuEl.style.maxHeight = previousMaxHeight;
		return size;
	}

	function updatePosition() {
		if (!menuEl) return;

		if (inlineAbove) {
			if (matchWidth && anchor instanceof HTMLElement) {
				anchorWidth = anchor.getBoundingClientRect().width;
			}
			ready = true;
			return;
		}

		let ax: number;
		let anchorTop: number;
		let anchorBottom: number;

		if (anchor instanceof HTMLElement) {
			const rect = anchor.getBoundingClientRect();
			ax = rect.left;
			anchorTop = rect.top;
			anchorBottom = rect.bottom;
			if (matchWidth) anchorWidth = rect.width;
		} else {
			ax = anchor.x;
			anchorTop = anchor.y;
			anchorBottom = anchor.y;
		}

		const { width: mw, height: mh } = measureMenu();
		const viewport = visualViewportRect();
		const viewportRight = viewport.left + viewport.width;
		const viewportBottom = viewport.top + viewport.height;
		const layoutViewportHeight = window.innerHeight;
		const pad = 8;
		const gap = 4;

		// Horizontal: align to start or end of anchor
		if (align === 'end' && anchor instanceof HTMLElement) {
			const rect = anchor.getBoundingClientRect();
			ax = rect.right - mw;
		}
		if (ax + mw > viewportRight - pad) ax = viewportRight - mw - pad;
		if (ax < viewport.left + pad) ax = viewport.left + pad;

		// Vertical: collision detection
		const spaceAbove = anchorTop - viewport.top - gap - pad;
		const spaceBelow = viewportBottom - anchorBottom - gap - pad;

		let availableHeight: number;

		if (forceAbove || (preferAbove && (mh <= spaceAbove || spaceAbove >= spaceBelow))) {
			availableHeight = spaceAbove;
			pos = {
				x: ax,
				bottom: Math.max(pad, layoutViewportHeight - anchorTop + gap)
			};
		} else {
			if (mh <= spaceBelow) {
				availableHeight = spaceBelow;
				const nextTop = Math.min(anchorBottom + gap, viewportBottom - pad - mh);
				pos = { x: ax, top: Math.max(nextTop, viewport.top + pad) };
			} else {
				availableHeight = spaceAbove;
				pos = {
					x: ax,
					bottom: Math.max(pad, layoutViewportHeight - anchorTop + gap)
				};
			}
		}

		menuMaxHeight =
			availableHeight >= 0 && (forceAbove || mh > availableHeight || menuMaxHeight != null)
				? Math.max(0, availableHeight)
				: undefined;
		ready = true;
	}

	function scheduleUpdate() {
		if (frame != null) cancelAnimationFrame(frame);
		frame = requestAnimationFrame(() => {
			frame = undefined;
			updatePosition();
		});
	}

	function scheduleSettledUpdates() {
		for (const timer of settleTimers) window.clearTimeout(timer);
		settleTimers = [];
		scheduleUpdate();
		for (const delay of [50, 150, 300]) {
			settleTimers.push(window.setTimeout(scheduleUpdate, delay));
		}
	}

	function handleViewportChange() {
		const nextViewportState = viewportState();
		if (nextViewportState === lastViewportState) return;
		lastViewportState = nextViewportState;
		scheduleSettledUpdates();
	}

	function handleFocusIn(event: FocusEvent) {
		if (event.target instanceof Node && menuEl?.contains(event.target)) {
			scheduleSettledUpdates();
		}
	}

	function trackAnchor() {
		const nextAnchorState = anchorState();
		if (nextAnchorState !== lastAnchorState) {
			lastAnchorState = nextAnchorState;
			updatePosition();
		}
		anchorFrame = requestAnimationFrame(trackAnchor);
	}

	function scrollMenuItemIntoView() {
		if (!listEl) return;
		const target =
			listEl.querySelector<HTMLElement>('[data-menu-highlighted="true"]') ??
			listEl.querySelector<HTMLElement>('[data-menu-active="true"]');
		target?.scrollIntoView({ block: scrollActiveBlock });
	}

	onMount(() => {
		let dvhProbe: HTMLDivElement | undefined;
		let dvhObserver: ResizeObserver | undefined;

		lastViewportState = viewportState();
		lastAnchorState = anchorState();
		scheduleUpdate();
		anchorFrame = requestAnimationFrame(trackAnchor);

		// Follow anchor on scroll/resize
		window.addEventListener('scroll', scheduleUpdate, true);
		window.addEventListener('resize', scheduleSettledUpdates);
		window.visualViewport?.addEventListener('resize', scheduleSettledUpdates);
		window.visualViewport?.addEventListener('scroll', scheduleUpdate);
		document.addEventListener('focusin', handleFocusIn);

		if ('ResizeObserver' in window) {
			dvhProbe = document.createElement('div');
			dvhProbe.style.position = 'fixed';
			dvhProbe.style.left = '-1px';
			dvhProbe.style.top = '0';
			dvhProbe.style.width = '1px';
			dvhProbe.style.height = '100dvh';
			dvhProbe.style.pointerEvents = 'none';
			dvhProbe.style.visibility = 'hidden';
			document.body.appendChild(dvhProbe);

			dvhObserver = new ResizeObserver(handleViewportChange);
			dvhObserver.observe(dvhProbe);
		}

		return () => {
			if (frame != null) cancelAnimationFrame(frame);
			if (anchorFrame != null) cancelAnimationFrame(anchorFrame);
			for (const timer of settleTimers) window.clearTimeout(timer);
			dvhObserver?.disconnect();
			dvhProbe?.remove();
			window.removeEventListener('scroll', scheduleUpdate, true);
			window.removeEventListener('resize', scheduleSettledUpdates);
			window.visualViewport?.removeEventListener('resize', scheduleSettledUpdates);
			window.visualViewport?.removeEventListener('scroll', scheduleUpdate);
			document.removeEventListener('focusin', handleFocusIn);
		};
	});

	$effect(() => {
		maxHeight;
		if (menuEl) scheduleSettledUpdates();
	});

	$effect(() => {
		if (!scrollActiveIntoView || !ready) return;
		items;
		void tick().then(() => {
			scrollMenuItemIntoView();
		});
	});
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	use:portal={!inlineAbove}
	class="fixed inset-0 z-[1000]"
	onclick={onclose}
	oncontextmenu={(e) => {
		e.preventDefault();
		onclose();
	}}
></div>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	use:portal={!inlineAbove}
	bind:this={menuEl}
	class="{inlineAbove
		? `absolute bottom-full mb-1 ${align === 'end' ? 'right-0' : 'left-0'}`
		: 'fixed'} app-theme app-surface z-[1001] min-w-36 rounded-xl border shadow-xl p-0.5 flex flex-col overflow-hidden {className}"
	style="{inlineAbove
		? ''
		: `left: ${pos.x}px; ${pos.bottom != null ? `bottom: ${pos.bottom}px;` : `top: ${pos.top ?? -9999}px;`} ${
				menuMaxHeight ? `max-height: ${menuMaxHeight}px;` : ''
			}`} {anchorWidth ? `width: ${anchorWidth}px;` : ''} opacity: {ready
		? 1
		: 0}; pointer-events: {ready ? 'auto' : 'none'};"
	onclick={(e) => e.stopPropagation()}
	onmousedown={(e) => e.stopPropagation()}
>
	{#if header}
		<div class="flex-none">
			{@render header()}
			{#if headerDivider}
				<div class="app-divider h-px mx-1 my-0.5"></div>
			{/if}
		</div>
	{/if}

	<div
		bind:this={listEl}
		class="flex-1 min-h-0 overflow-y-auto"
		style={maxHeight ? `max-height: ${maxHeight};` : ''}
	>
		{#if children}
			{@render children()}
		{:else if items.length === 0 && empty}
			{@render empty()}
		{:else}
			{#each items as item}
				{#if item.divider}
					<div class="app-divider h-px mx-1 my-0.5"></div>
				{:else}
					<div
						class="group menu-row flex items-center gap-1 w-full h-6 rounded-xl text-xs transition-colors duration-75 {item.active ||
						item.highlighted
							? 'app-interactive-active'
							: ''}"
						data-menu-highlighted={item.highlighted ? 'true' : undefined}
						data-menu-active={item.active ? 'true' : undefined}
					>
						<button
							class="flex items-center gap-2 min-w-0 flex-1 h-full px-2 text-inherit"
							use:tooltip={item.tooltip ? { content: item.tooltip, placement: 'top' } : null}
							onclick={() => {
								item.onclick();
								onclose();
							}}
						>
							{#if item.image}
								<img src={item.image} alt="" class="w-4 h-4 rounded-full object-cover shrink-0" />
							{:else if item.icon}
								<Icon name={item.icon} size={14} />
							{/if}
							<span class="flex-1 text-left truncate">{item.label}</span>
							{#if item.shortcut}
								<KeyPill text={item.shortcut} class="ml-auto shrink-0" />
							{/if}
							{#if item.check && item.active}
								<svg
									class="app-icon-muted w-3 h-3 shrink-0"
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
						{#if item.actionIcon && item.actionOnclick}
							<button
								class="app-icon-muted app-interactive flex items-center justify-center w-5 h-5 mr-0.5 rounded-lg shrink-0 transition-all duration-75"
								aria-label={item.actionLabel}
								onclick={(e) => {
									e.stopPropagation();
									item.actionOnclick?.(e.currentTarget as HTMLElement);
								}}
							>
								<Icon name={item.actionIcon} size={12} />
							</button>
						{/if}
					</div>
				{/if}
			{/each}
		{/if}
	</div>

	{#if footer}
		<div class="flex-none">
			{#if footerDivider}
				<div class="app-divider h-px mx-1 my-0.5"></div>
			{/if}
			{@render footer()}
		</div>
	{/if}
</div>

<style>
	.menu-row {
		color: color-mix(in oklab, var(--app-fg) 62%, var(--app-bg));
	}

	.menu-row:hover {
		background: color-mix(in oklab, var(--app-fg) 6%, transparent);
		color: var(--app-fg);
	}
</style>
