<script lang="ts">
	/**
	 * Global running-agents panel: every active chat + running subagent in
	 * one place, with Open / Stop actions. Live-updates via the events:chat
	 * socket (chat:active, chat:subagents, done) plus a slow poll fallback.
	 */
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import Modal from './Modal.svelte';
	import Icon from './Icon.svelte';
	import Spinner from './common/Spinner.svelte';
	import {
		listActiveAgents,
		cancelTask,
		cancelChatSubagent,
		type ActiveChatEntry,
		type SubagentInfo
	} from '$lib/apis/chat';
	import { getPathDisplayName } from '$lib/utils/paths';
	import { socketStore } from '$lib/stores/socket.svelte';
	import { requestConfirm } from '$lib/stores/confirm';
	import { t } from '$lib/i18n';
	import { toast } from 'svelte-sonner';

	interface Props {
		onclose: () => void;
	}

	let { onclose }: Props = $props();

	let activeChats = $state<ActiveChatEntry[]>([]);
	let subagents = $state<SubagentInfo[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let stopping = $state<Set<string>>(new Set());
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	function markStopping(id: string) {
		stopping = new Set(stopping).add(id);
	}

	function unmarkStopping(id: string) {
		const next = new Set(stopping);
		next.delete(id);
		stopping = next;
	}

	async function refresh() {
		try {
			const data = await listActiveAgents();
			activeChats = data.active_chats ?? [];
			subagents = (data.subagents ?? []).filter(
				(s) => s.status === 'starting' || s.status === 'running'
			);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	const subagentsByChat = $derived.by(() => {
		const map = new Map<string, SubagentInfo[]>();
		for (const s of subagents) {
			const key = s.parent_chat_id ?? '';
			const list = map.get(key) ?? [];
			list.push(s);
			map.set(key, list);
		}
		return map;
	});
	const orphanSubagents = $derived(subagentsByChat.get('') ?? []);

	const totalRunning = $derived(activeChats.length + subagents.length);

	function openChat(chat: ActiveChatEntry) {
		onclose();
		const params = new URLSearchParams();
		if (chat.workspace) params.set('workspace', chat.workspace);
		params.set('chatId', chat.chat_id);
		goto(`/?${params.toString()}`);
	}

	async function stopChat(chat: ActiveChatEntry) {
		if (!chat.message_id) return;
		const confirmed = await requestConfirm({
			title: $t('agents.stopChatTitle'),
			message: $t('agents.stopChatConfirm', { title: chat.title || chat.chat_id }),
			cancelLabel: $t('common.cancel'),
			confirmLabel: $t('agents.stop')
		});
		if (!confirmed) return;
		markStopping(`chat:${chat.chat_id}`);
		try {
			await cancelTask(chat.chat_id, chat.message_id);
			await refresh();
		} catch (e) {
			toast.error(e instanceof Error ? e.message : String(e));
		} finally {
			unmarkStopping(`chat:${chat.chat_id}`);
		}
	}

	async function stopSubagent(subagent: SubagentInfo) {
		const chatId = subagent.parent_chat_id;
		if (!chatId) return;
		const confirmed = await requestConfirm({
			title: $t('chat.subagentsKillTitle'),
			message: $t('chat.subagentsKillConfirm', { task: (subagent.task ?? '').slice(0, 80) }),
			cancelLabel: $t('common.cancel'),
			confirmLabel: $t('chat.subagentsKill')
		});
		if (!confirmed) return;
		markStopping(`sub:${subagent.delegation_id}`);
		try {
			await cancelChatSubagent(chatId, subagent.delegation_id);
			await refresh();
		} catch (e) {
			toast.error(e instanceof Error ? e.message : String(e));
		} finally {
			unmarkStopping(`sub:${subagent.delegation_id}`);
		}
	}

	function handleSocketEvent(data: { type?: string; done?: boolean }) {
		if (!data?.type) return;
		if (data.type === 'chat:active' || data.type === 'chat:subagents' || data.done === true) {
			void refresh();
		}
	}

	onMount(() => {
		void refresh();
		const off = socketStore.on('events:chat', handleSocketEvent);
		pollTimer = setInterval(() => void refresh(), 15000);
		return () => {
			off();
			if (pollTimer) clearInterval(pollTimer);
		};
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});
</script>

<Modal
	{onclose}
	class="w-full max-w-[35rem] mx-4 max-md:mx-0 max-md:rounded-none max-h-[32rem] max-md:max-h-dvh flex flex-col mb-[6vh] max-md:mb-0"
>
	<div class="flex items-center gap-2 px-4 py-3 border-b border-gray-100 dark:border-white/8">
		<Icon name="spark" size={14} class="text-gray-400 shrink-0" />
		<span class="text-sm font-medium text-gray-800 dark:text-gray-200">
			{$t('agents.runningTitle')}
		</span>
		{#if !loading && !error}
			<span class="text-xs text-gray-400 dark:text-gray-600">
				{$t('agents.runningCount', { count: totalRunning })}
			</span>
		{/if}
		<button
			type="button"
			class="ml-auto flex size-6 items-center justify-center rounded-full text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
			onclick={onclose}
			aria-label={$t('common.close')}
		>
			<Icon name="xmark" size={14} />
		</button>
	</div>

	<div class="min-h-0 flex-1 overflow-y-auto px-2 py-2">
		{#if loading}
			<div class="flex items-center justify-center py-10">
				<Spinner size={20} />
			</div>
		{:else if error}
			<div class="px-2 py-6 text-center">
				<p class="text-xs text-gray-500 dark:text-gray-400">{$t('agents.loadFailed')}</p>
				<p class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">{error}</p>
				<button
					type="button"
					class="mt-3 rounded-lg px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-500/10 dark:text-gray-300 transition-colors"
					onclick={() => {
						loading = true;
						void refresh();
					}}
				>
					{$t('agents.retry')}
				</button>
			</div>
		{:else if totalRunning === 0}
			<div class="px-2 py-8 text-center text-xs text-gray-400 dark:text-gray-600">
				{$t('agents.nothingRunning')}
			</div>
		{:else}
			<div class="space-y-1">
				{#each activeChats as chat (chat.chat_id)}
					{@const chatSubs = subagentsByChat.get(chat.chat_id) ?? []}
					<div class="rounded-xl border border-gray-100 dark:border-white/8 px-3 py-2">
						<div class="flex items-center gap-2 min-w-0">
							<span
								class="size-2 shrink-0 rounded-full bg-emerald-500 animate-pulse"
								aria-hidden="true"
							></span>
							<div class="min-w-0 flex-1">
								<div class="truncate text-xs font-medium text-gray-800 dark:text-gray-200">
									{chat.title || $t('chat.fallbackTitle')}
								</div>
								{#if chat.workspace}
									<div class="truncate text-[0.6875rem] text-gray-400 dark:text-gray-600">
										{getPathDisplayName(chat.workspace, chat.workspace)}
									</div>
								{/if}
							</div>
							<button
								type="button"
								class="shrink-0 rounded-md px-2 py-0.5 text-[0.6875rem] font-medium text-gray-500 hover:bg-gray-500/10 dark:text-gray-400 transition-colors"
								onclick={() => openChat(chat)}
							>
								{$t('agents.open')}
							</button>
							{#if chat.message_id}
								<button
									type="button"
									disabled={stopping.has(`chat:${chat.chat_id}`)}
									class="shrink-0 rounded-md px-2 py-0.5 text-[0.6875rem] font-medium text-red-500 hover:bg-red-500/10 disabled:opacity-50 transition-colors"
									onclick={() => stopChat(chat)}
								>
									{stopping.has(`chat:${chat.chat_id}`) ? $t('agents.stopping') : $t('agents.stop')}
								</button>
							{/if}
						</div>
						{#each chatSubs as sub (sub.delegation_id)}
							<div class="mt-1.5 ml-4 flex items-center gap-2 min-w-0">
								<span
									class="size-2 shrink-0 rounded-full border border-dashed border-current text-gray-400"
									aria-hidden="true"
								></span>
								<span
									class="min-w-0 flex-1 truncate text-[0.6875rem] text-gray-500 dark:text-gray-400"
								>
									{sub.task || $t('agents.subagentFallback')}
								</span>
								<button
									type="button"
									disabled={stopping.has(`sub:${sub.delegation_id}`)}
									class="shrink-0 rounded-md px-2 py-0.5 text-[0.6875rem] font-medium text-red-500 hover:bg-red-500/10 disabled:opacity-50 transition-colors"
									onclick={() => stopSubagent(sub)}
								>
									{stopping.has(`sub:${sub.delegation_id}`)
										? $t('agents.stopping')
										: $t('agents.stop')}
								</button>
							</div>
						{/each}
					</div>
				{/each}
				{#each orphanSubagents as sub (sub.delegation_id)}
					<div
						class="rounded-xl border border-gray-100 dark:border-white/8 px-3 py-2 flex items-center gap-2 min-w-0"
					>
						<span
							class="size-2 shrink-0 rounded-full border border-dashed border-current text-gray-400"
							aria-hidden="true"
						></span>
						<span class="min-w-0 flex-1 truncate text-xs text-gray-600 dark:text-gray-400">
							{sub.task || $t('agents.subagentFallback')}
						</span>
						{#if sub.parent_chat_id}
							<button
								type="button"
								disabled={stopping.has(`sub:${sub.delegation_id}`)}
								class="shrink-0 rounded-md px-2 py-0.5 text-[0.6875rem] font-medium text-red-500 hover:bg-red-500/10 disabled:opacity-50 transition-colors"
								onclick={() => stopSubagent(sub)}
							>
								{stopping.has(`sub:${sub.delegation_id}`)
									? $t('agents.stopping')
									: $t('agents.stop')}
							</button>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</div>
</Modal>
