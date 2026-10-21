/**
 * Cached terminal sessions.
 *
 * A TerminalSession owns the xterm instance, the WebSocket, and all
 * streaming state for one PTY session. It is keyed by session id and
 * survives Svelte component unmounts: switching tabs, switching
 * workspaces, or crossing the wide-screen breakpoint detaches the DOM
 * but keeps the terminal (and its scrollback) and the socket alive, so
 * switching back is instant and loses nothing. Page reloads resume from
 * the server's retained output window via the byte-offset protocol.
 *
 * Protocol (matches cptr/routers/terminal.py):
 *   Client → Server:  ?offset=<consumed bytes> on the URL
 *   Server → Client:  one JSON text frame {"type":"sync","offset":N}
 *                     then raw PTY output bytes.
 */
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebglAddon } from '@xterm/addon-webgl';

// ── Compact binary WebSocket protocol (client → server) ─────────────
//   0x00 + raw input bytes (microtask-batched for throughput)
//   0x02 + uint16 cols + uint16 rows, big-endian (5 bytes total)
//   0x03 stop, 0x04 force stop
const MSG_INPUT = 0;
const MSG_RESIZE = 2;
const MSG_STOP = 3;
const MSG_FORCE_STOP = 4;

const INPUT_BUF_CAP = 4096;
/** Cap on cached sessions; least-recently-attached idle ones are evicted. */
const MAX_CACHED_SESSIONS = 24;
/** Finger movement (px) before a touch becomes a scroll, so taps still click. */
const TOUCH_SCROLL_SLOP = 4;
const LINE_PX = 13 * 1.3; // fontSize * lineHeight

const textEncoder = new TextEncoder();

export interface TerminalSessionOptions {
	/** Custom WS path (command sessions); defaults to /api/terminal/{id}/ws. */
	wsPath?: string;
	/** Already-rendered output to seed a fresh terminal with. */
	initialOutput?: string;
	/** Absolute byte offset of initialOutput in the session stream. */
	initialOffset?: number;
	readOnly?: boolean;
}

function themeColor(name: '--app-bg' | '--app-fg') {
	return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function withAlpha(color: string, alpha: number) {
	const hex = /^#([0-9a-f]{6})$/i.exec(color)?.[1];
	if (!hex) return color;
	return `rgba(${parseInt(hex.slice(0, 2), 16)}, ${parseInt(hex.slice(2, 4), 16)}, ${parseInt(hex.slice(4, 6), 16)}, ${alpha})`;
}

function terminalTheme() {
	const background = themeColor('--app-bg');
	const foreground = themeColor('--app-fg');
	return {
		background,
		foreground,
		cursor: foreground,
		cursorAccent: background,
		selectionBackground: withAlpha(foreground, 0.2)
	};
}

export class TerminalSession {
	readonly term: Terminal;
	private fitAddon: FitAddon;
	/** Host div we own: term.open() runs once here; we re-parent it on attach. */
	private host = document.createElement('div');
	private ws: WebSocket | null = null;
	private container: HTMLElement | null = null;
	private resizeObserver: ResizeObserver | null = null;
	private themeObserver: MutationObserver | null = null;
	private resizeTimeout: ReturnType<typeof setTimeout> | null = null;
	private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	private destroyed = false;
	/** Server confirmed the session is gone; stop reconnecting, keep buffer. */
	private sessionEnded = false;

	/** Absolute offset of consumed PTY output; drives resume-on-reconnect. */
	private consumed = 0;
	private lastSentCols = 0;
	private lastSentRows = 0;
	private webgl: WebglAddon | null = null;

	private inputBuf = new Uint8Array(INPUT_BUF_CAP);
	private inputBufLen = 0;
	private inputFlushScheduled = false;

	private readonly resizeBuf = new ArrayBuffer(5);
	private readonly resizeView = new DataView(this.resizeBuf);

	// ── Wake lock ────────────────────────────────────────────
	private wakeLock: WakeLockSentinel | null = null;

	// ── Haptics ──────────────────────────────────────────────
	private lastOutputTime = 0;
	private outputActive = false;
	private hapticTimer: ReturnType<typeof setTimeout> | null = null;
	private static readonly HAPTIC_PAUSE_MS = 800;

	private containerPointerDown: ((e: PointerEvent) => void) | null = null;
	private touchStartX = 0;
	private touchStartY = 0;
	private lastTouchY = 0;
	private touchAccum = 0;
	private touchingTerminal = false;
	private touchScrolling = false;

	constructor(
		readonly key: string,
		private opts: TerminalSessionOptions
	) {
		this.host.style.height = '100%';
		this.consumed = opts.initialOffset ?? 0;

		this.term = new Terminal({
			cursorBlink: true,
			cursorStyle: 'bar',
			fontFamily: '"JetBrains Mono", "Fira Code", ui-monospace, monospace',
			fontSize: 13,
			lineHeight: 1.3,
			scrollback: 10000,
			macOptionClickForcesSelection: true,
			disableStdin: opts.readOnly ?? false,
			theme: terminalTheme()
		});

		this.fitAddon = new FitAddon();
		this.term.loadAddon(this.fitAddon);
		this.term.open(this.host);
		if (opts.initialOutput) this.term.write(opts.initialOutput);

		this.inputBuf[0] = MSG_INPUT;
		this.resizeView.setUint8(0, MSG_RESIZE);

		// Watch for theme changes
		this.themeObserver = new MutationObserver(() => {
			if (!this.destroyed) this.term.options.theme = terminalTheme();
		});
		this.themeObserver.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ['class', 'style']
		});

		// Handle macOS key combos that need special escape sequences
		this.term.attachCustomKeyEventHandler((e: KeyboardEvent) => {
			if (e.type !== 'keydown') return true;
			if (e.metaKey) {
				if (e.key === 'ArrowLeft') {
					this.sendInput('\x01'); // Ctrl-A: beginning of line
					return false;
				}
				if (e.key === 'ArrowRight') {
					this.sendInput('\x05'); // Ctrl-E: end of line
					return false;
				}
			}
			if (e.altKey) {
				if (e.key === 'ArrowLeft') {
					this.sendInput('\x1bb'); // ESC-b: backward word
					return false;
				}
				if (e.key === 'ArrowRight') {
					this.sendInput('\x1bf'); // ESC-f: forward word
					return false;
				}
				if (e.key === 'Backspace') {
					this.sendInput('\x17'); // Ctrl-W: delete word backward
					return false;
				}
			}
			return true;
		});

		// Input/resize handlers registered ONCE per session — never
		// re-registered on reconnect, so rich CLI apps (Claude Code, htop)
		// never receive duplicate SIGWINCH.
		this.term.onData((data: string) => this.sendInput(data));
		this.term.onResize(({ cols, rows }: { cols: number; rows: number }) => {
			if (cols === this.lastSentCols && rows === this.lastSentRows) return;
			this.lastSentCols = cols;
			this.lastSentRows = rows;
			this.sendResize(cols, rows);
		});

		document.addEventListener('visibilitychange', this.handleVisibilityChange);
		this.acquireWakeLock();
		this.connect();
	}

	// ── Attach / detach / dispose ─────────────────────────────

	/** Re-parent the terminal into a live container and resume sizing/input. */
	attach(container: HTMLElement, opts?: TerminalSessionOptions): void {
		if (this.destroyed) return;
		if (opts?.readOnly !== undefined) this.setReadOnly(opts.readOnly);
		this.lastAttachedAt = Date.now();
		if (this.container === container) return;
		this.detachDom();
		this.container = container;
		container.appendChild(this.host);

		// Clicks anywhere in the terminal area focus the helper textarea,
		// so keystrokes always reach the PTY (fixes "keyboard does nothing"
		// after remounts and clicks in the padding area).
		this.containerPointerDown = () => this.term.focus();
		container.addEventListener('pointerdown', this.containerPointerDown);

		document.addEventListener('touchstart', this.onTouchStart, { passive: true });
		document.addEventListener('touchmove', this.onTouchMove, { passive: false });
		document.addEventListener('touchend', this.onTouchEnd, { passive: true });
		window.addEventListener('cptr:terminal-input', this.onShortcutInput);

		// Fit once layout settles; repaint so a re-attached WebGL surface
		// (or a canvas fallback after context loss) shows current content.
		requestAnimationFrame(() => {
			this.doFit();
			this.tryLoadWebgl();
			this.term.refresh(0, Math.max(0, this.term.rows - 1));
		});

		this.resizeObserver = new ResizeObserver(() => this.debouncedFit());
		this.resizeObserver.observe(container);
	}

	/** Remove from the DOM but keep the terminal and socket running. */
	detach(): void {
		this.detachDom();
		this.ensureConnected();
	}

	private detachDom(): void {
		if (this.resizeTimeout) {
			clearTimeout(this.resizeTimeout);
			this.resizeTimeout = null;
		}
		this.resizeObserver?.disconnect();
		this.resizeObserver = null;
		if (this.container && this.containerPointerDown) {
			this.container.removeEventListener('pointerdown', this.containerPointerDown);
		}
		this.containerPointerDown = null;
		document.removeEventListener('touchstart', this.onTouchStart);
		document.removeEventListener('touchmove', this.onTouchMove);
		document.removeEventListener('touchend', this.onTouchEnd);
		window.removeEventListener('cptr:terminal-input', this.onShortcutInput);
		this.host.remove();
		this.container = null;
	}

	/** Permanent teardown — close tab, evicted cache entry, dead session. */
	dispose(): void {
		if (this.destroyed) return;
		this.destroyed = true;
		sessions.delete(this.key);
		this.detachDom();
		document.removeEventListener('visibilitychange', this.handleVisibilityChange);
		if (this.resizeTimeout) clearTimeout(this.resizeTimeout);
		if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
		if (this.hapticTimer) clearTimeout(this.hapticTimer);
		this.themeObserver?.disconnect();
		this.releaseWakeLock();
		if (this.ws) {
			this.ws.onmessage = null;
			this.ws.onclose = null;
			this.ws.onerror = null;
			this.ws.onopen = null;
			this.ws.close();
			this.ws = null;
		}
		this.webgl?.dispose();
		this.webgl = null;
		this.term.dispose();
	}

	get attached(): boolean {
		return this.container !== null;
	}

	get lastAttached(): number {
		return this.lastAttachedAt;
	}
	private lastAttachedAt = Date.now();

	// ── WebSocket ────────────────────────────────────────────

	private wsUrl(): string {
		const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
		const path = this.opts.wsPath || `/api/terminal/${this.key}/ws`;
		const url = new URL(path, `${window.location.protocol}//${window.location.host}`);
		if (this.consumed > 0) url.searchParams.set('offset', String(this.consumed));
		url.protocol = proto;
		return url.toString();
	}

	private ensureConnected(): void {
		if (this.destroyed || this.sessionEnded) return;
		if (
			!this.ws ||
			this.ws.readyState === WebSocket.CLOSED ||
			this.ws.readyState === WebSocket.CLOSING
		) {
			if (this.reconnectTimer) {
				clearTimeout(this.reconnectTimer);
				this.reconnectTimer = null;
			}
			this.connect();
		}
	}

	private connect(): void {
		if (this.destroyed) return;
		const url = this.wsUrl();
		const ws = new WebSocket(url);
		ws.binaryType = 'arraybuffer';
		this.ws = ws;

		ws.onopen = () => {
			if (this.destroyed || this.ws !== ws) return;
			// Send ONE resize message with current dimensions. Do NOT fit
			// here; that would trigger onResize and a second SIGWINCH.
			this.lastSentCols = this.term.cols;
			this.lastSentRows = this.term.rows;
			this.sendResize(this.term.cols, this.term.rows);
		};

		ws.onmessage = (event) => {
			if (this.destroyed || this.ws !== ws) return;
			if (typeof event.data === 'string') {
				// Control frame: absolute offset of the bytes that follow.
				try {
					const msg = JSON.parse(event.data);
					if (msg && msg.type === 'sync' && typeof msg.offset === 'number') {
						this.consumed = msg.offset;
					}
				} catch {
					// ignore malformed control frames
				}
				return;
			}
			const data = new Uint8Array(event.data as ArrayBuffer);
			this.consumed += data.byteLength;
			this.term.write(data);
			this.trackOutputForHaptics();
		};

		ws.onclose = (e) => {
			if (this.destroyed || this.ws !== ws) return;
			this.ws = null;
			if ((e.code === 4004 || e.code === 4001) && !this.sessionEnded) {
				// Session gone (server restart, tab closed elsewhere) or
				// unauthorized: stop reconnecting but keep the terminal and
				// its scrollback on screen so it stays readable/copyable
				// until the tab is closed (which disposes the cache entry).
				this.sessionEnded = true;
				const notice =
					e.code === 4001
						? '\r\n\x1b[2m[terminal session unauthorized]\x1b[0m\r\n'
						: '\r\n\x1b[2m[terminal session ended]\x1b[0m\r\n';
				this.term.write(notice);
				return;
			}
			this.reconnectTimer = setTimeout(() => {
				this.reconnectTimer = null;
				this.connect();
			}, 2000);
		};

		ws.onerror = () => {
			// onclose always follows; reconnect handled there.
		};
	}

	// ── Input / resize / control ─────────────────────────────

	private flushInput(): void {
		this.inputFlushScheduled = false;
		if (!this.inputBufLen || this.ws?.readyState !== WebSocket.OPEN) {
			this.inputBufLen = 0;
			return;
		}
		this.ws.send(this.inputBuf.subarray(0, 1 + this.inputBufLen));
		this.inputBufLen = 0;
	}

	private sendInput(data: string): void {
		if (this.opts.readOnly) return;
		if (this.ws?.readyState !== WebSocket.OPEN) return;

		if (data.length === 1 && data.charCodeAt(0) < 128) {
			if (1 + this.inputBufLen + 1 > INPUT_BUF_CAP) this.flushInput();
			this.inputBuf[1 + this.inputBufLen++] = data.charCodeAt(0);
		} else {
			const encoded = textEncoder.encode(data);
			if (1 + this.inputBufLen + encoded.length > INPUT_BUF_CAP) this.flushInput();
			this.inputBuf.set(encoded, 1 + this.inputBufLen);
			this.inputBufLen += encoded.length;
		}

		if (!this.inputFlushScheduled) {
			this.inputFlushScheduled = true;
			queueMicrotask(() => this.flushInput());
		}
	}

	private sendResize(cols: number, rows: number): void {
		if (this.ws?.readyState !== WebSocket.OPEN) return;
		this.resizeView.setUint16(1, cols, false);
		this.resizeView.setUint16(3, rows, false);
		this.ws.send(this.resizeBuf);
	}

	sendStop(): void {
		if (this.ws?.readyState === WebSocket.OPEN) {
			this.ws.send(new Uint8Array([MSG_STOP]));
		}
	}

	sendForceStop(): void {
		if (this.ws?.readyState === WebSocket.OPEN) {
			this.ws.send(new Uint8Array([MSG_FORCE_STOP]));
		}
	}

	setReadOnly(readOnly: boolean): void {
		this.opts = { ...this.opts, readOnly };
		this.term.options.disableStdin = readOnly;
	}

	// ── Sizing / rendering ───────────────────────────────────

	private doFit(): void {
		if (!this.container || this.destroyed) return;
		const rect = this.container.getBoundingClientRect();
		if (rect.width === 0 || rect.height === 0) return;
		try {
			this.fitAddon.fit();
		} catch {
			// FitAddon can throw if terminal not properly attached
		}
	}

	private debouncedFit(): void {
		if (this.resizeTimeout) clearTimeout(this.resizeTimeout);
		// 350ms debounce, long enough to outlast the mobile keyboard
		// animation so we only refit ONCE after it fully settles.
		this.resizeTimeout = setTimeout(() => {
			this.resizeTimeout = null;
			this.doFit();
		}, 350);
	}

	/** GPU rendering with automatic fallback + recovery on context loss. */
	private tryLoadWebgl(): void {
		if (this.destroyed || this.webgl) return;
		try {
			const webgl = new WebglAddon();
			webgl.onContextLoss(() => {
				// Drop the dead addon (xterm falls back to canvas) and force
				// a full repaint — without this the screen freezes on the
				// last WebGL frame after tab hide/show or GPU eviction.
				webgl.dispose();
				this.webgl = null;
				if (!this.destroyed) {
					this.term.refresh(0, Math.max(0, this.term.rows - 1));
				}
			});
			this.term.loadAddon(webgl);
			this.webgl = webgl;
		} catch {
			// WebGL unavailable or terminal not sized yet; canvas is fine.
		}
	}

	// ── Mobile touch handling ────────────────────────────────
	// Document-level listeners guarantee events regardless of which child
	// element the touch lands on. A small slop threshold keeps taps
	// producing click events (no preventDefault until real scrolling
	// starts), which fixes tapping TUI buttons/links on touch devices.

	private isAltBuffer(): boolean {
		try {
			return this.term.buffer.active.type === 'alternate';
		} catch {
			return false;
		}
	}

	private onTouchStart = (e: TouchEvent) => {
		if (e.touches.length !== 1 || !this.container) return;
		const target = e.target as HTMLElement;
		if (!this.container.contains(target)) return;
		this.touchingTerminal = true;
		this.touchScrolling = false;
		this.touchStartX = e.touches[0].clientX;
		this.touchStartY = e.touches[0].clientY;
		this.lastTouchY = this.touchStartY;
		this.touchAccum = 0;
	};

	private onTouchMove = (e: TouchEvent) => {
		if (!this.touchingTerminal || e.touches.length !== 1) return;
		const currentY = e.touches[0].clientY;
		if (!this.touchScrolling) {
			const dx = Math.abs(e.touches[0].clientX - this.touchStartX);
			const dy = Math.abs(currentY - this.touchStartY);
			if (dx < TOUCH_SCROLL_SLOP && dy < TOUCH_SCROLL_SLOP) return;
			this.touchScrolling = true;
			this.lastTouchY = currentY;
			this.touchAccum = 0;
		}
		e.preventDefault(); // block pull-to-refresh / page scroll

		const deltaY = this.lastTouchY - currentY; // positive = finger up = scroll down
		this.lastTouchY = currentY;
		this.touchAccum += deltaY;

		const lines = Math.trunc(this.touchAccum / LINE_PX);
		if (lines !== 0) {
			if (this.isAltBuffer()) {
				// TUI apps (vim, htop, Claude Code): send arrow keys
				const seq = lines > 0 ? '\x1b[B' : '\x1b[A';
				const count = Math.min(Math.abs(lines), 5);
				for (let i = 0; i < count; i++) this.sendInput(seq);
			} else {
				this.term.scrollLines(lines);
			}
			this.touchAccum -= lines * LINE_PX;
		}
	};

	private onTouchEnd = () => {
		this.touchingTerminal = false;
		this.touchScrolling = false;
	};

	// ── ShortcutBar input ────────────────────────────────────

	private onShortcutInput = (e: Event) => {
		if (!this.isVisible()) return;
		const data = (e as CustomEvent).detail;
		if (typeof data === 'string') this.sendInput(data);
	};

	/** True when this session's container is actually shown to the user. */
	private isVisible(): boolean {
		if (!this.container || !this.container.isConnected) return false;
		// Hidden tabs use visibility:hidden on an ancestor (.persisted-tab-hidden);
		// only the visible terminal should receive ShortcutBar input.
		let el: HTMLElement | null = this.container;
		while (el) {
			if (window.getComputedStyle(el).visibility !== 'visible') return false;
			el = el.parentElement;
		}
		return true;
	}

	// ── Wake lock / visibility / haptics ─────────────────────

	private async acquireWakeLock(): Promise<void> {
		if (this.wakeLock || this.destroyed) return;
		try {
			if ('wakeLock' in navigator) {
				this.wakeLock = await navigator.wakeLock.request('screen');
				this.wakeLock.addEventListener('release', () => {
					this.wakeLock = null;
				});
			}
		} catch {
			// Can fail if document is hidden or permission denied
		}
	}

	private releaseWakeLock(): void {
		this.wakeLock?.release().catch(() => {});
		this.wakeLock = null;
	}

	private handleVisibilityChange = () => {
		if (document.visibilityState === 'visible' && !this.destroyed) {
			this.acquireWakeLock();
			// Reconnect if the browser killed the socket while hidden
			// (background tabs are throttled/suspended).
			this.ensureConnected();
		}
	};

	private trackOutputForHaptics(): void {
		const now = Date.now();
		if (now - this.lastOutputTime > 2000) this.outputActive = true;
		this.lastOutputTime = now;

		if (this.hapticTimer) clearTimeout(this.hapticTimer);
		this.hapticTimer = setTimeout(() => {
			this.hapticTimer = null;
			if (this.outputActive && document.hidden) {
				if ('vibrate' in navigator) navigator.vibrate(15);
			}
			this.outputActive = false;
		}, TerminalSession.HAPTIC_PAUSE_MS);
	}
}

// ── Session cache ───────────────────────────────────────────────

const sessions = new Map<string, TerminalSession>();

/**
 * Get (or lazily create) the cached session for a key. Evicts the
 * least-recently-attached idle sessions above MAX_CACHED_SESSIONS.
 */
export function acquireTerminalSession(
	key: string,
	opts: TerminalSessionOptions = {}
): TerminalSession {
	let session = sessions.get(key);
	if (!session) {
		evictIfNeeded();
		session = new TerminalSession(key, opts);
		sessions.set(key, session);
	}
	return session;
}

/** Permanently dispose the cached session (call when its tab closes). */
export function disposeTerminalSession(key: string): void {
	sessions.get(key)?.dispose();
}

function evictIfNeeded(): void {
	if (sessions.size < MAX_CACHED_SESSIONS) return;
	const idle = [...sessions.values()]
		.filter((s) => !s.attached)
		.sort((a, b) => a.lastAttached - b.lastAttached);
	const over = sessions.size - MAX_CACHED_SESSIONS + 1;
	for (let i = 0; i < Math.min(over, idle.length); i++) {
		idle[i].dispose();
	}
}
