<script lang="ts">
	import { tick } from 'svelte';
	import { confirmDialog, settleConfirm } from '$lib/stores/confirm';

	let confirmButton: HTMLButtonElement | undefined = $state();

	$effect(() => {
		if (!$confirmDialog) return;
		void tick().then(() => confirmButton?.focus());
	});

	function handleKeydown(event: KeyboardEvent) {
		if (!$confirmDialog) return;
		if (event.key === 'Escape') {
			event.preventDefault();
			settleConfirm(false);
		}
		if (event.key === 'Enter') {
			event.preventDefault();
			settleConfirm(true);
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if $confirmDialog}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/45 px-3 py-4"
		onmousedown={() => settleConfirm(false)}
		onkeydown={() => {}}
	>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="confirm-dialog app-theme app-surface w-full max-w-sm rounded-xl border p-3.5 shadow-2xl"
			style="background: var(--app-bg); color: var(--app-fg);"
			role="dialog"
			aria-modal="true"
			aria-labelledby="confirm-dialog-title"
			aria-describedby="confirm-dialog-message"
			tabindex="-1"
			onmousedown={(event) => event.stopPropagation()}
			onkeydown={() => {}}
		>
			<div class="min-w-0">
				<h2 id="confirm-dialog-title" class="text-sm font-medium text-gray-900 dark:text-white">
					{$confirmDialog.title}
				</h2>
				<p
					id="confirm-dialog-message"
					class="mt-2 text-xs leading-relaxed text-gray-500 dark:text-gray-400"
				>
					{$confirmDialog.message}
				</p>
			</div>

			<div class="mt-4 flex justify-end gap-2">
				<button
					class="app-muted app-interactive h-7 rounded-lg px-3 text-xs font-medium transition-colors duration-75"
					type="button"
					onclick={() => settleConfirm(false)}
				>
					{$confirmDialog.cancelLabel}
				</button>
				<button
					bind:this={confirmButton}
					class="h-7 rounded-lg bg-gray-900 px-3 text-xs font-medium text-white transition-colors duration-75 hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-gray-200"
					type="button"
					onclick={() => settleConfirm(true)}
				>
					{$confirmDialog.confirmLabel}
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.confirm-dialog {
		animation: confirm-dialog-in 0.1s ease-out;
	}

	@keyframes confirm-dialog-in {
		from {
			opacity: 0;
			transform: scale(0.985);
		}
		to {
			opacity: 1;
			transform: scale(1);
		}
	}
</style>
