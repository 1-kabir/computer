<script lang="ts">
	import Icon from '../Icon.svelte';
	import { t } from '$lib/i18n';
	import { openChatTab } from '$lib/stores';
	import { requestConfirm } from '$lib/stores/confirm';
	import type { SubagentInfo } from '$lib/apis/chat';

	interface Props {
		subagents: SubagentInfo[];
		onkill?: (delegationId: string) => void;
	}

	let { subagents, onkill }: Props = $props();
	let collapsed = $state(false);
	let cancelling = $state<Set<string>>(new Set());

	const running = $derived(
		subagents.filter((s) => s.status === 'starting' || s.status === 'running')
	);
	const runningCount = $derived(running.length);
	// The bar is an ACTIVE-work surface: list only starting/running delegations.
	// Finished ones drop off (their transcript lives in the subagent chat) so the
	// list doesn't pile up rows the user can no longer act on.
	const totalCount = $derived(running.length);

	function statusIcon(status: SubagentInfo['status']): 'spin' | 'dash' | 'x' {
		// Bar only lists active delegations; interrupted/error get their marks,
		// everything else spins until it resolves.
		if (status === 'interrupted') return 'dash';
		if (status === 'error') return 'x';
		return 'spin';
	}

	async function openSubagent(subagent: SubagentInfo) {
		if (!subagent.subagent_chat_id) return;
		openChatTab(subagent.subagent_chat_id);
	}

	async function killSubagent(subagent: SubagentInfo) {
		const confirmed = await requestConfirm({
			title: $t('chat.subagentsKillTitle'),
			message: $t('chat.subagentsKillConfirm', { task: subagent.task.slice(0, 80) }),
			cancelLabel: $t('common.cancel'),
			confirmLabel: $t('chat.subagentsKill')
		});
		if (!confirmed) return;
		cancelling = new Set(cancelling).add(subagent.delegation_id);
		try {
			await onkill?.(subagent.delegation_id);
		} finally {
			const next = new Set(cancelling);
			next.delete(subagent.delegation_id);
			cancelling = next;
		}
	}
</script>

{#if totalCount > 0}
	<div class="app-subtle-surface my-1 overflow-hidden rounded-3xl border shadow-sm">
		<div class="flex items-center justify-between px-3.5 pt-1.5 pb-1">
			<div class="app-muted flex min-w-0 items-center gap-2 text-xs">
				<span
					class="size-3 rounded-full border-2 border-current border-t-transparent opacity-70 animate-spin"
				></span>
				<span class="truncate">
					{$t('chat.subagentsRunning', { count: runningCount })}
				</span>
			</div>
			<button
				type="button"
				class="app-muted flex size-6 items-center justify-center rounded-full bg-transparent transition-colors hover:text-gray-600 dark:hover:text-gray-300"
				onclick={() => (collapsed = !collapsed)}
				aria-label={collapsed ? $t('chat.subagentsExpand') : $t('chat.subagentsCollapse')}
			>
				<Icon name={collapsed ? 'chevron-down' : 'chevron-up'} size={12} />
			</button>
		</div>

		{#if !collapsed}
			<div class="space-y-1 px-2.5 pb-3">
				{#each running as subagent (subagent.delegation_id)}
					<div class="flex items-start gap-2 rounded-2xl px-1 py-0.5 text-xs">
						<span class="app-muted mt-0.5 flex size-3.5 shrink-0 items-center justify-center">
							{#if cancelling.has(subagent.delegation_id)}
								<span
									class="size-3 rounded-full border-2 border-current border-t-transparent opacity-70 animate-spin"
								></span>
							{:else if statusIcon(subagent.status) === 'spin'}
								<span
									class="size-3 rounded-full border-2 border-current border-t-transparent opacity-70 animate-spin"
								></span>
							{:else if statusIcon(subagent.status) === 'dash'}
								<span class="size-3 rounded-full border border-dashed border-current"></span>
							{:else}
								<Icon name="x" size={14} strokeWidth={2.5} />
							{/if}
						</span>
						<span class="line-clamp-2 min-w-0 flex-1 text-gray-700 dark:text-gray-300">
							{subagent.task}
						</span>
						<span class="flex shrink-0 items-center gap-1">
							{#if subagent.subagent_chat_id}
								<button
									type="button"
									class="rounded px-1.5 py-0.5 text-[0.6875rem] font-medium text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-white/8 hover:bg-gray-200 dark:hover:bg-white/12 transition-colors duration-100"
									onclick={() => openSubagent(subagent)}
								>
									{$t('chat.subagentsOpen')}
								</button>
							{/if}
							{#if subagent.status === 'starting' || subagent.status === 'running'}
								<button
									type="button"
									class="rounded px-1.5 py-0.5 text-[0.6875rem] font-medium text-red-500 dark:text-red-400 hover:bg-red-500/10 transition-colors duration-100"
									disabled={cancelling.has(subagent.delegation_id)}
									onclick={() => killSubagent(subagent)}
								>
									{$t('chat.subagentsKill')}
								</button>
							{/if}
						</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>
{/if}
