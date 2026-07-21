<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { t } from '$lib/i18n';

	interface BrowserState {
		url: string;
		title: string;
		can_go_back: boolean;
		can_go_forward: boolean;
	}

	interface Props {
		sessionId: string;
		active: boolean;
		onstate: (state: BrowserState) => void;
		onstatus: (status: string, message?: string, mode?: 'proxy') => void;
		onquality: (quality: 'low' | 'balanced' | 'crisp' | null) => void;
		ondevicemode: (mode: DeviceMode, viewport?: MobileViewport) => void;
	}

	type DeviceMode = 'auto' | 'desktop' | 'mobile';
	type MobileViewport = { width: number; height: number };

	interface DeviceProfile {
		userAgent: string;
		language: string;
		maxTouchPoints: number;
		screenWidth: number;
		screenHeight: number;
		devicePixelRatio: number;
		orientation: 'portraitPrimary' | 'landscapePrimary';
		mobile: boolean;
		userAgentMetadata?: Record<string, unknown>;
	}

	interface UserAgentData {
		brands?: Array<{ brand: string; version: string }>;
		mobile?: boolean;
		platform?: string;
		getHighEntropyValues?: (hints: string[]) => Promise<Record<string, unknown>>;
	}

	interface EditableRegion {
		x: number;
		y: number;
		width: number;
		height: number;
	}

	let { sessionId, active, onstate, onstatus, onquality, ondevicemode }: Props = $props();
	let canvas: HTMLCanvasElement;
	let container: HTMLDivElement;
	let keyboardAntenna: HTMLTextAreaElement;
	let socket: WebSocket | undefined;
	let decoder: VideoDecoder | undefined;
	let audioDecoder: AudioDecoder | undefined;
	let audioContext: AudioContext | undefined;
	let observer: ResizeObserver | undefined;
	let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
	let viewportTimer: ReturnType<typeof setTimeout> | undefined;
	let reconnectAttempt = 0;
	let disposed = false;
	let viewport = { width: 0, height: 0 };
	let pendingPointer: Record<string, unknown> | undefined;
	let pointerFrame = 0;
	let hasFrame = false;
	let awaitingFrame = true;
	let ready = $state(false);
	let managed = $state(false);
	let mobile = $state(false);
	let quality = $state<'low' | 'balanced' | 'crisp'>('balanced');
	let deviceMode = $state<DeviceMode>('auto');
	let mobileViewport = $state<MobileViewport | undefined>();
	let deviceProfile: DeviceProfile | undefined;
	let editableRegions = $state<EditableRegion[]>([]);
	let lastViewport = '';
	let composition = false;
	let suppressText = '';
	let keepKeyboardFocus = false;
	let audioClockOrigin: number | undefined;
	let reapplyViewportAfterConfig = true;
	const pressedKeys = new Map<string, Record<string, unknown>>();
	const touchPoints = new Map<number, Record<string, unknown>>();
	const macClient = /Mac|iPhone|iPad/.test(navigator.userAgent);
	const keyboardSentinel = '\u200b';

	function send(message: Record<string, unknown>) {
		if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
	}

	async function collectDeviceProfile(): Promise<DeviceProfile> {
		const uaData = (navigator as Navigator & { userAgentData?: UserAgentData }).userAgentData;
		let highEntropy: Record<string, unknown> = {};
		if (uaData?.getHighEntropyValues) {
			try {
				highEntropy = await uaData.getHighEntropyValues([
					'architecture',
					'bitness',
					'fullVersionList',
					'model',
					'platformVersion'
				]);
			} catch {
				// User agent client hints are optional.
			}
		}
		const maxTouchPoints = Math.min(10, Math.max(0, navigator.maxTouchPoints || 0));
		const uaMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
		const mobileProfile =
			uaData?.mobile ?? (uaMobile || (/Mac/.test(navigator.userAgent) && maxTouchPoints > 1));
		const orientation = screen.orientation?.type?.startsWith('landscape')
			? 'landscapePrimary'
			: 'portraitPrimary';
		const metadata = uaData
			? {
					brands: uaData.brands || [],
					mobile: Boolean(uaData.mobile),
					platform: uaData.platform || '',
					...highEntropy
				}
			: undefined;
		return {
			userAgent: navigator.userAgent,
			language: navigator.language,
			maxTouchPoints,
			screenWidth: screen.width,
			screenHeight: screen.height,
			devicePixelRatio: window.devicePixelRatio || 1,
			orientation,
			mobile: mobileProfile,
			userAgentMetadata: metadata
		};
	}

	const deviceProfilePromise = collectDeviceProfile().then((profile) => {
		deviceProfile = profile;
		mobile = profile.mobile;
		return profile;
	});

	function connect() {
		if (disposed) return;
		ready = false;
		lastViewport = '';
		reapplyViewportAfterConfig = true;
		awaitingFrame = true;
		onstatus(hasFrame ? 'playing' : 'reconnecting');
		const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
		socket = new WebSocket(`${scheme}://${location.host}/api/browser/sessions/${sessionId}/stream`);
		socket.binaryType = 'arraybuffer';
		socket.onopen = () => {
			reconnectAttempt = 0;
			send({ type: 'visibility', visible: active });
		};
		socket.onmessage = receive;
		socket.onclose = () => {
			if (disposed) return;
			onstatus('lost', 'Chrome connection lost');
			const delays = [1000, 2000, 5000];
			reconnectTimer = setTimeout(connect, delays[Math.min(reconnectAttempt++, delays.length - 1)]);
		};
	}

	function receive(event: MessageEvent) {
		if (typeof event.data === 'string') {
			const message = JSON.parse(event.data);
			if (message.type === 'ready') {
				ready = true;
				managed = message.managed === true;
				if (message.quality?.preset in { low: true, balanced: true, crisp: true })
					quality = message.quality.preset;
				if (message.device_mode in { auto: true, desktop: true, mobile: true })
					deviceMode = message.device_mode;
				if (
					Number.isFinite(message.mobile_viewport?.width) &&
					Number.isFinite(message.mobile_viewport?.height)
				)
					mobileViewport = message.mobile_viewport;
				else mobileViewport = undefined;
				onquality(managed ? quality : null);
				ondevicemode(deviceMode, mobileViewport);
				if (active) focusBrowser();
				else resize();
			}
			if (message.type === 'state') onstate(message);
			if (message.type === 'status' && (message.status !== 'playing' || hasFrame))
				onstatus(message.status, message.message, message.mode);
			if (message.type === 'config') configureDecoder(message);
			if (message.type === 'audio_config') configureAudio(message);
			if (message.type === 'editable_regions' && Array.isArray(message.regions)) {
				editableRegions = message.regions.filter(
					(region: unknown): region is EditableRegion =>
						typeof region === 'object' &&
						region !== null &&
						Number.isFinite((region as EditableRegion).x) &&
						Number.isFinite((region as EditableRegion).y) &&
						Number.isFinite((region as EditableRegion).width) &&
						Number.isFinite((region as EditableRegion).height)
				);
			}
			if (
				message.type === 'quality' &&
				message.preset in { low: true, balanced: true, crisp: true }
			) {
				quality = message.preset;
				onquality(quality);
			}
			return;
		}
		if (!(event.data instanceof ArrayBuffer) || event.data.byteLength < 14) return;
		const view = new DataView(event.data);
		const media = view.getUint8(0);
		const timestamp = Number(view.getBigUint64(6));
		if (media === 1 && decoder?.state === 'configured') {
			try {
				decoder.decode(
					new EncodedVideoChunk({
						type: view.getUint8(1) & 1 ? 'key' : 'delta',
						timestamp,
						data: new Uint8Array(event.data, 14)
					})
				);
			} catch {
				send({ type: 'request_keyframe' });
			}
		} else if (media === 2 && audioDecoder?.state === 'configured') {
			try {
				audioDecoder.decode(
					new EncodedAudioChunk({ type: 'key', timestamp, data: new Uint8Array(event.data, 14) })
				);
			} catch {
				audioClockOrigin = undefined;
			}
		}
	}

	function configureDecoder(config: { codec: string; width: number; height: number }) {
		decoder?.close();
		decoder = new VideoDecoder({
			output(frame) {
				if (!canvas) return frame.close();
				canvas.width = frame.displayWidth;
				canvas.height = frame.displayHeight;
				canvas.getContext('2d', { alpha: false })?.drawImage(frame, 0, 0);
				frame.close();
				if (awaitingFrame) {
					awaitingFrame = false;
					hasFrame = true;
					onstatus('playing');
				}
			},
			error() {
				send({ type: 'request_keyframe' });
			}
		});
		decoder.configure({ codec: config.codec, optimizeForLatency: true });
		if (managed && reapplyViewportAfterConfig) {
			reapplyViewportAfterConfig = false;
			void sendViewport(true);
		}
	}

	function configureAudio(config: { codec: string; sampleRate: number; numberOfChannels: number }) {
		if (!('AudioDecoder' in window)) return;
		audioDecoder?.close();
		audioClockOrigin = undefined;
		audioDecoder = new AudioDecoder({
			output(data) {
				playAudio(data);
				data.close();
			},
			error() {
				audioClockOrigin = undefined;
			}
		});
		try {
			audioDecoder.configure({
				codec: config.codec,
				sampleRate: config.sampleRate,
				numberOfChannels: config.numberOfChannels
			});
		} catch {
			audioDecoder.close();
			audioDecoder = undefined;
		}
	}

	function unlockAudio() {
		if (!('AudioContext' in window)) return;
		audioContext ??= new AudioContext();
		if (audioContext.state !== 'running') void audioContext.resume();
	}

	function playAudio(data: AudioData) {
		const context = audioContext;
		if (!context || context.state !== 'running') return;
		const mediaTime = data.timestamp / 1_000_000;
		audioClockOrigin ??= context.currentTime + 0.1 - mediaTime;
		let start = audioClockOrigin + mediaTime;
		if (start < context.currentTime - 0.05) {
			audioClockOrigin = context.currentTime + 0.1 - mediaTime;
			start = context.currentTime + 0.1;
		}
		const buffer = context.createBuffer(
			data.numberOfChannels,
			data.numberOfFrames,
			data.sampleRate
		);
		for (let channel = 0; channel < data.numberOfChannels; channel += 1) {
			data.copyTo(buffer.getChannelData(channel), { planeIndex: channel, format: 'f32-planar' });
		}
		const source = context.createBufferSource();
		source.buffer = buffer;
		source.connect(context.destination);
		source.start(Math.max(context.currentTime, start));
	}

	function resize(debounce = false) {
		if (!container || !ready) return;
		const rect = container.getBoundingClientRect();
		const width = Math.round(rect.width);
		const height = Math.round(rect.height);
		if (width <= 0 || height <= 0) return;
		viewport = {
			width,
			height
		};
		if (viewportTimer) clearTimeout(viewportTimer);
		if (debounce || (isMobileInput() && document.activeElement === keyboardAntenna)) {
			viewportTimer = setTimeout(() => void sendViewport(), 150);
		} else {
			void sendViewport();
		}
	}

	async function sendViewport(force = false) {
		const device = deviceProfile || (await deviceProfilePromise);
		const key = JSON.stringify([viewport, quality, device, deviceMode, mobileViewport]);
		if (!force && key === lastViewport) return;
		lastViewport = key;
		send({
			type: 'viewport',
			...viewport,
			quality,
			device,
			device_mode: deviceMode,
			mobile_viewport: mobileViewport ?? null,
			force
		});
	}

	function modifiers(event: MouseEvent | KeyboardEvent | WheelEvent) {
		return (
			(event.altKey ? 1 : 0) |
			(event.ctrlKey ? 2 : 0) |
			(event.metaKey ? 4 : 0) |
			(event.shiftKey ? 8 : 0)
		);
	}

	function coordinates(event: MouseEvent | PointerEvent | WheelEvent) {
		const rect = canvas.getBoundingClientRect();
		return {
			x: (event.clientX - rect.left) / rect.width,
			y: (event.clientY - rect.top) / rect.height,
			normalized: true
		};
	}

	function button(value: number) {
		return value === 0 ? 'left' : value === 1 ? 'middle' : value === 2 ? 'right' : 'none';
	}

	function focusAntenna() {
		if (!isMobileInput()) return;
		keepKeyboardFocus = true;
		resetKeyboardAntenna();
		keyboardAntenna?.focus({ preventScroll: true });
		unlockAudio();
	}

	function resetKeyboardAntenna() {
		if (!keyboardAntenna) return;
		keyboardAntenna.value = keyboardSentinel;
		keyboardAntenna.setSelectionRange(keyboardSentinel.length, keyboardSentinel.length);
	}

	function canvasFocus() {
		if (keepKeyboardFocus) focusAntenna();
	}

	function isEditableTouch(event: PointerEvent) {
		if (!isMobileInput()) return false;
		const point = coordinates(event);
		return editableRegions.some(
			(region) =>
				point.x >= region.x &&
				point.x <= region.x + region.width &&
				point.y >= region.y &&
				point.y <= region.y + region.height
		);
	}

	function touch(event: PointerEvent, type: 'start' | 'move' | 'end' | 'cancel') {
		if (type === 'start') {
			event.preventDefault();
			unlockAudio();
			keepKeyboardFocus = isEditableTouch(event);
			if (keepKeyboardFocus) focusAntenna();
			canvas.setPointerCapture(event.pointerId);
			touchPoints.set(event.pointerId, {
				id: event.pointerId,
				...coordinates(event),
				radiusX: event.width / 2,
				radiusY: event.height / 2,
				force: event.pressure
			});
		} else if (type === 'move') {
			if (!touchPoints.has(event.pointerId)) return;
			touchPoints.set(event.pointerId, {
				id: event.pointerId,
				...coordinates(event),
				radiusX: event.width / 2,
				radiusY: event.height / 2,
				force: event.pressure
			});
		} else {
			touchPoints.delete(event.pointerId);
		}
		send({
			type: 'touch',
			event: type,
			...coordinates(event),
			points: [...touchPoints.values()],
			button: type === 'move' ? 'none' : 'left',
			buttons: event.buttons,
			click_count: event.detail || 1,
			modifiers: modifiers(event)
		});
	}

	function pointer(event: PointerEvent, type: 'move' | 'down' | 'up' | 'cancel') {
		if (isMobileInput()) {
			touch(event, type === 'down' ? 'start' : type === 'up' ? 'end' : type);
			return;
		}
		if (type === 'cancel') return;
		if (type === 'down') {
			unlockAudio();
			focusBrowser();
			canvas.focus();
			canvas.setPointerCapture(event.pointerId);
		}
		const message = {
			type: 'pointer',
			event: type,
			...coordinates(event),
			button: type === 'move' ? 'none' : button(event.button),
			buttons: event.buttons,
			click_count: event.detail || 1,
			modifiers: modifiers(event)
		};
		if (type !== 'move') return send(message);
		pendingPointer = message;
		if (!pointerFrame) {
			pointerFrame = requestAnimationFrame(() => {
				if (pendingPointer) send(pendingPointer);
				pendingPointer = undefined;
				pointerFrame = 0;
			});
		}
	}

	function wheel(event: WheelEvent) {
		event.preventDefault();
		focusBrowser();
		send({
			type: 'wheel',
			...coordinates(event),
			delta_x: event.deltaX,
			delta_y: event.deltaY,
			modifiers: modifiers(event)
		});
	}

	function key(event: KeyboardEvent, type: 'keyDown' | 'keyUp') {
		event.preventDefault();
		event.stopPropagation();
		const message = {
			type: 'key',
			event: type,
			key: event.key,
			code: event.code,
			unmodified_text: event.key.length === 1 ? event.key : '',
			text:
				type === 'keyDown' &&
				event.key.length === 1 &&
				!event.altKey &&
				!event.ctrlKey &&
				!event.metaKey
					? event.key
					: '',
			modifiers: modifiers(event),
			auto_repeat: event.repeat,
			location: event.location,
			is_keypad: event.location === KeyboardEvent.DOM_KEY_LOCATION_NUMPAD,
			windows_virtual_key_code: event.keyCode,
			primary_modifier: macClient ? event.metaKey : event.ctrlKey,
			primary_modifier_key: event.key === (macClient ? 'Meta' : 'Control')
		};
		if (type === 'keyDown') pressedKeys.set(event.code, message);
		else pressedKeys.delete(event.code);
		send(message);
	}

	function mobileKey(key: string, code: string, virtualKey: number) {
		for (const event of ['keyDown', 'keyUp']) {
			send({
				type: 'key',
				event,
				key,
				code,
				text: '',
				unmodified_text: '',
				modifiers: 0,
				location: 0,
				is_keypad: false,
				windows_virtual_key_code: virtualKey
			});
		}
	}

	function beforeInput(event: InputEvent) {
		if (!isMobileInput() || composition) return;
		const text = event.data || '';
		if (suppressText && text === suppressText) {
			event.preventDefault();
			suppressText = '';
			resetKeyboardAntenna();
			return;
		}
		switch (event.inputType) {
			case 'insertText':
				event.preventDefault();
				if (text) send({ type: 'text', text });
				break;
			case 'insertFromPaste':
				event.preventDefault();
				if (text) send({ type: 'paste', text });
				break;
			case 'insertLineBreak':
			case 'insertParagraph':
				event.preventDefault();
				mobileKey('Enter', 'Enter', 13);
				break;
			case 'deleteContentBackward':
				event.preventDefault();
				mobileKey('Backspace', 'Backspace', 8);
				break;
			case 'deleteContentForward':
				event.preventDefault();
				mobileKey('Delete', 'Delete', 46);
				break;
			default:
				return;
		}
		resetKeyboardAntenna();
	}

	function releaseKeys() {
		for (const message of pressedKeys.values()) send({ ...message, event: 'keyUp', text: '' });
		pressedKeys.clear();
	}

	export function navigate(url: string) {
		focusBrowser();
		send({ type: 'navigate', url });
	}
	export function back() {
		focusBrowser();
		send({ type: 'back' });
	}
	export function forward() {
		focusBrowser();
		send({ type: 'forward' });
	}
	export function reload() {
		focusBrowser();
		send({ type: 'reload' });
	}
	export function setQuality(nextQuality: 'low' | 'balanced' | 'crisp') {
		quality = nextQuality;
		lastViewport = '';
		onquality(quality);
		resize();
	}
	export function setDeviceMode(nextMode: DeviceMode) {
		deviceMode = nextMode;
		lastViewport = '';
		ondevicemode(deviceMode, mobileViewport);
		resize();
	}
	export function setMobileViewport(nextViewport?: MobileViewport) {
		if (nextViewport && (nextViewport.width <= 0 || nextViewport.height <= 0)) return;
		mobileViewport = nextViewport;
		lastViewport = '';
		ondevicemode(deviceMode, mobileViewport);
		resize();
	}
	function isMobileInput() {
		return managed && (deviceMode === 'mobile' || (deviceMode === 'auto' && mobile));
	}
	function focusBrowser() {
		if (!ready) return;
		send({ type: 'focus' });
		resize();
	}

	function visualViewportResize() {
		resize(true);
	}

	$effect(() => {
		if (socket?.readyState === WebSocket.OPEN && ready) {
			send({ type: 'visibility', visible: active });
			if (active) focusBrowser();
		}
	});
	onMount(() => {
		observer = new ResizeObserver(() => resize());
		observer.observe(container);
		window.visualViewport?.addEventListener('resize', visualViewportResize);
		resetKeyboardAntenna();
		connect();
	});

	onDestroy(() => {
		disposed = true;
		releaseKeys();
		observer?.disconnect();
		window.visualViewport?.removeEventListener('resize', visualViewportResize);
		if (reconnectTimer) clearTimeout(reconnectTimer);
		if (viewportTimer) clearTimeout(viewportTimer);
		if (pointerFrame) cancelAnimationFrame(pointerFrame);
		socket?.close();
		decoder?.close();
		audioDecoder?.close();
		if (audioContext) void audioContext.close();
	});
</script>

<div class="chrome-container" bind:this={container}>
	<textarea
		bind:this={keyboardAntenna}
		class="keyboard-antenna"
		aria-label={$t('browser.remoteKeyboard')}
		onbeforeinput={beforeInput}
		oncompositionstart={() => (composition = true)}
		oncompositionupdate={() => {}}
		oncompositionend={(event) => {
			composition = false;
			const text = event.data || '';
			if (isMobileInput() && text) {
				suppressText = text;
				send({ type: 'text', text });
				queueMicrotask(() => {
					if (suppressText === text) suppressText = '';
				});
			}
			resetKeyboardAntenna();
		}}
		oninput={() => {
			if (!composition) resetKeyboardAntenna();
		}}
	></textarea>
	<canvas
		bind:this={canvas}
		tabindex={isMobileInput() ? -1 : 0}
		onfocus={canvasFocus}
		onpointermove={(event) => pointer(event, 'move')}
		onpointerdown={(event) => pointer(event, 'down')}
		onpointerup={(event) => pointer(event, 'up')}
		onpointercancel={(event) => pointer(event, 'cancel')}
		onwheel={wheel}
		onkeydown={(event) => key(event, 'keyDown')}
		onkeyup={(event) => key(event, 'keyUp')}
		onblur={releaseKeys}
		onpaste={(event) => {
			event.preventDefault();
			event.stopPropagation();
			send({ type: 'paste', text: event.clipboardData?.getData('text/plain') || '' });
		}}
		oncontextmenu={(event) => event.preventDefault()}
	></canvas>
</div>

<style>
	.chrome-container,
	canvas {
		width: 100%;
		height: 100%;
		display: block;
		background: var(--app-bg);
	}
	.chrome-container {
		position: relative;
		display: grid;
		place-items: center;
	}
	canvas {
		width: auto;
		height: auto;
		max-width: 100%;
		max-height: 100%;
		margin: auto;
		outline: none;
		object-fit: contain;
		touch-action: none;
	}
	.keyboard-antenna {
		position: absolute;
		top: 0;
		left: 0;
		width: 1px;
		height: 1px;
		opacity: 0;
		pointer-events: none;
	}
</style>
