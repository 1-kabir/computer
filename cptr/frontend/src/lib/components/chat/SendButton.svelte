<script lang="ts">
	import { t } from '$lib/i18n';

	interface Props {
		canSend: boolean;
		streaming: boolean;
		onsend: () => void;
		oncancel?: () => void;
		onvoice?: () => void;
		voiceActive?: boolean;
	}
	let { canSend, streaming, onsend, oncancel, onvoice, voiceActive = false }: Props = $props();

	// Show send when there's sendable text, even during streaming (enqueue).
	// Show stop only when streaming with nothing to send.
	const showStop = $derived(streaming && !canSend && !!oncancel);
	const showVoice = $derived(!streaming && !canSend && !!onvoice);
</script>

{#if showStop}
	<button
		class="bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-white hover:bg-gray-200 dark:hover:bg-gray-600 transition rounded-full p-1"
		onclick={oncancel}
	>
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-4">
			<path
				fill-rule="evenodd"
				d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12zm6-2.438c0-.724.588-1.312 1.313-1.312h4.874c.725 0 1.313.588 1.313 1.313v4.874c0 .725-.588 1.313-1.313 1.313H9.564a1.312 1.312 0 01-1.313-1.313V9.564z"
				clip-rule="evenodd"
			/>
		</svg>
	</button>
{:else if showVoice}
	<button
		class="flex items-center justify-center rounded-full p-1 transition
			{voiceActive
			? 'bg-gray-900 text-white hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90'
			: 'bg-gray-100 text-gray-800 hover:bg-gray-200 dark:bg-white/90 dark:text-black dark:hover:bg-white'}"
		onclick={onvoice}
		aria-label={$t('admin.audio.voiceMode')}
		title={$t('admin.audio.voiceMode')}
	>
		<svg
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			stroke-width="2"
			stroke-linecap="round"
			stroke-linejoin="round"
			class="size-4 {voiceActive ? 'animate-pulse' : ''}"
		>
			<path d="M4 14V10" />
			<path d="M8 18V6" />
			<path d="M12 21V3" />
			<path d="M16 18V6" />
			<path d="M20 14V10" />
		</svg>
	</button>
{:else}
	<button
		class="{canSend
			? 'bg-black text-white hover:bg-gray-900 dark:bg-white dark:text-black dark:hover:bg-white/90'
			: 'text-white bg-gray-200 dark:text-gray-900 dark:bg-gray-700 cursor-default'} transition rounded-full p-1 self-center"
		onclick={onsend}
		disabled={!canSend}
	>
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-4">
			<path
				fill-rule="evenodd"
				d="M8 14a.75.75 0 0 1-.75-.75V4.56L4.03 7.78a.75.75 0 0 1-1.06-1.06l4.5-4.5a.75.75 0 0 1 1.06 0l4.5 4.5a.75.75 0 0 1-1.06 1.06L8.75 4.56v8.69A.75.75 0 0 1 8 14Z"
				clip-rule="evenodd"
			/>
		</svg>
	</button>
{/if}
