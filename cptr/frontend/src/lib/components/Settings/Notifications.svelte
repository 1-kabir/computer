<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { t } from '$lib/i18n';
	import {
		notificationsEnabled,
		notificationSound,
		bridgeNotificationsMuted
	} from '$lib/stores/chat';
	import { savePreferences } from '$lib/apis/state';
	import {
		createNotificationTarget,
		CHAT_NOTIFICATION_EVENTS,
		deleteNotificationTarget,
		listNotificationBotOptions,
		listNotificationEvents,
		listNotificationTargets,
		setDefaultNotificationTarget,
		testNotificationTarget,
		updateNotificationTarget,
		type BotOption,
		type ChatNotificationEvent,
		type NotificationEventOption,
		type NotificationDelivery,
		type NotificationTarget,
		type NotificationTargetType
	} from '$lib/apis/notifications';
	import Icon from '../Icon.svelte';
	import Modal from '../Modal.svelte';
	import ToggleSwitch from '../common/ToggleSwitch.svelte';

	const fallbackEventOptions: NotificationEventOption[] = [
		{
			event: CHAT_NOTIFICATION_EVENTS.FINISHED,
			label: 'Chat finished',
			description: 'A chat run finished successfully.'
		},
		{
			event: CHAT_NOTIFICATION_EVENTS.FAILED,
			label: 'Chat failed',
			description: 'A chat run failed.'
		}
	];

	let targets = $state<NotificationTarget[]>([]);
	let eventOptions = $state<NotificationEventOption[]>(fallbackEventOptions);
	let botOptions = $state<BotOption[]>([]);
	let loadingTargets = $state(false);
	let savingTarget = $state(false);
	let editingId = $state<string | null>(null);
	let formOpen = $state(false);
	let form = $state({
		id: '',
		type: 'webhook' as NotificationTargetType,
		url: '',
		bot_id: '',
		destination_chat_id: '',
		enabled: true,
		events: [] as ChatNotificationEvent[],
		delivery: 'away' as NotificationDelivery
	});
	let targetTypes = $derived<NotificationTargetType[]>(
		botOptions.length ? ['webhook', 'bot'] : ['webhook']
	);

	onMount(() => {
		void loadNotificationTargets();
	});

	async function loadNotificationTargets() {
		loadingTargets = true;
		const [targetResult, botResult, eventResult] = await Promise.allSettled([
			listNotificationTargets(),
			listNotificationBotOptions(),
			listNotificationEvents()
		]);
		if (targetResult.status === 'fulfilled') {
			targets = targetResult.value;
		} else {
			toast.error($t('general.notificationTargetsLoadFailed'));
		}
		botOptions = botResult.status === 'fulfilled' ? botResult.value : [];
		eventOptions =
			eventResult.status === 'fulfilled' && eventResult.value.length
				? eventResult.value
				: fallbackEventOptions;
		loadingTargets = false;
	}

	async function toggleNotifications() {
		if (!$notificationsEnabled) {
			if ('Notification' in window) {
				const permission = await Notification.requestPermission();
				if (permission === 'granted') {
					notificationsEnabled.set(true);
				} else {
					toast.error($t('general.notificationPermissionDenied'));
				}
			}
		} else {
			notificationsEnabled.set(false);
		}
	}

	function openNewTarget(type: NotificationTargetType = 'webhook') {
		if (type === 'bot' && !botOptions.length) type = 'webhook';
		editingId = null;
		formOpen = true;
		form = {
			id: '',
			type,
			url: '',
			bot_id: botOptions[0]?.id || '',
			destination_chat_id: '',
			enabled: true,
			events: [],
			delivery: 'away'
		};
	}

	function openEditTarget(target: NotificationTarget) {
		editingId = target.id;
		formOpen = true;
		form = {
			id: target.id,
			type: target.type,
			url: '',
			bot_id: target.config.bot_id || botOptions[0]?.id || '',
			destination_chat_id: target.config.destination_chat_id || '',
			enabled: target.enabled,
			events: [...target.events],
			delivery: target.delivery
		};
	}

	function toggleFormEvent(event: ChatNotificationEvent) {
		form.events = form.events.includes(event)
			? form.events.filter((item) => item !== event)
			: [...form.events, event];
	}

	async function saveTarget() {
		savingTarget = true;
		try {
			const originalId = editingId;
			const payload: any = {
				id: form.id.trim(),
				type: form.type,
				enabled: form.enabled,
				events: form.events,
				delivery: form.delivery
			};
			if (form.type === 'webhook') {
				if (!editingId || form.url.trim()) payload.config = { url: form.url.trim() };
			} else {
				payload.config = {
					bot_id: form.bot_id,
					destination_chat_id: form.destination_chat_id.trim()
				};
			}
			if (originalId) {
				await updateNotificationTarget(originalId, payload);
			} else {
				await createNotificationTarget(payload);
			}
			await loadNotificationTargets();
			formOpen = false;
			toast.success($t('settings.saved'));
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : $t('general.notificationTargetSaveFailed')
			);
		} finally {
			savingTarget = false;
		}
	}

	async function patchTarget(target: NotificationTarget, patch: Partial<NotificationTarget>) {
		try {
			await updateNotificationTarget(target.id, patch as any);
			await loadNotificationTargets();
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : $t('general.notificationTargetSaveFailed')
			);
		}
	}

	async function removeTarget(target: NotificationTarget) {
		try {
			await deleteNotificationTarget(target.id);
			await loadNotificationTargets();
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : $t('general.notificationTargetSaveFailed')
			);
		}
	}

	async function makeDefault(target: NotificationTarget) {
		try {
			await setDefaultNotificationTarget(target.id);
			await loadNotificationTargets();
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : $t('general.notificationTargetSaveFailed')
			);
		}
	}

	async function sendTest(target: NotificationTarget) {
		try {
			await testNotificationTarget(target.id);
			toast.success($t('general.testSent'));
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : $t('general.notificationTargetSaveFailed')
			);
		}
	}
</script>

<div class="flex flex-col h-full">
	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5 -mr-1.5">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
			{$t('general.notifications')}
		</h2>

		<div class="flex flex-col gap-2.5">
			<label class="flex items-center justify-between cursor-pointer">
				<span class="text-xs text-gray-600 dark:text-gray-400">
					{$t('general.browserNotifications')}
				</span>
				<ToggleSwitch value={$notificationsEnabled} onchange={() => toggleNotifications()} />
			</label>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
				{$t('general.browserNotificationsDesc')}
			</p>

			<label class="flex items-center justify-between cursor-pointer">
				<span class="text-xs text-gray-600 dark:text-gray-400">
					{$t('general.notificationSound')}
				</span>
				<ToggleSwitch value={$notificationSound} onchange={(v) => notificationSound.set(v)} />
			</label>

			<label class="flex items-center justify-between cursor-pointer">
				<span class="text-xs text-gray-600 dark:text-gray-400">
					{$t('general.bridgeNotificationsMuted')}
				</span>
				<ToggleSwitch
					value={$bridgeNotificationsMuted}
					onchange={(v) => bridgeNotificationsMuted.set(v)}
				/>
			</label>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
				{$t('general.bridgeNotificationsMutedDesc')}
			</p>

			<div class="mt-3 flex items-center justify-between">
				<span class="text-xs font-medium text-gray-700 dark:text-gray-300">
					{$t('general.notificationTargets')}
				</span>
				<button
					class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75"
					onclick={() => openNewTarget()}
					title={$t('general.addNotificationTarget')}
					aria-label={$t('general.addNotificationTarget')}
				>
					<Icon name="plus" size={14} />
				</button>
			</div>

			{#if loadingTargets}
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600">{$t('common.loading')}</p>
			{:else if !targets.length}
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
					{$t('general.noNotificationTargets')}
				</p>
			{:else}
				<div class="flex flex-col">
					{#each targets as target}
						{@const targetDestination =
							target.type === 'webhook'
								? target.config.url_masked
								: target.config.destination_chat_id}
						{@const alertLabels = eventOptions
							.filter((event) => target.events.includes(event.event))
							.map((event) => event.label)
							.join(', ')}
						<div class="notification-target-row">
							<div class="min-w-0 flex-1">
								<div class="flex items-center gap-2 min-w-0">
									<span class="truncate text-[0.71875rem] text-gray-700 dark:text-gray-300">
										{target.id}
									</span>
									<span class="shrink-0 text-[0.625rem] text-gray-400 dark:text-gray-600">
										{target.type === 'webhook' ? $t('general.webhook') : $t('general.bot')}
									</span>
									{#if target.is_default}
										<span class="shrink-0 text-[0.625rem] text-gray-400 dark:text-gray-600">
											{$t('general.default')}
										</span>
									{/if}
								</div>
								<div
									class="truncate text-[0.625rem] text-gray-400 dark:text-gray-600 leading-tight"
								>
									{targetDestination}
								</div>
								<div
									class="truncate text-[0.625rem] text-gray-400 dark:text-gray-600 leading-tight"
								>
									{alertLabels || $t('general.noChatAlerts')}
									{#if target.events.length}
										· {target.delivery === 'away'
											? $t('general.onlyWhenAway')
											: $t('general.always')}
									{/if}
								</div>
							</div>

							<div class="flex items-center gap-2 shrink-0">
								<button
									class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
									onclick={() => sendTest(target)}
								>
									{$t('general.sendTest')}
								</button>
								{#if !target.is_default}
									<button
										class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
										onclick={() => makeDefault(target)}
									>
										{$t('general.makeDefault')}
									</button>
								{/if}
								<button
									class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
									onclick={() => openEditTarget(target)}
								>
									{$t('common.edit')}
								</button>
								<button
									class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
									onclick={() => removeTarget(target)}
								>
									{$t('common.remove')}
								</button>
							</div>

							<div class="w-9 shrink-0 flex justify-end">
								<ToggleSwitch
									value={target.enabled}
									onchange={(v) => patchTarget(target, { enabled: v })}
								/>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>
</div>

{#if formOpen}
	<Modal onclose={() => (formOpen = false)} class="w-full max-w-md mx-4">
		<div class="p-4">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-3">
				{editingId ? $t('common.edit') : $t('general.addNotificationTarget')}
			</h2>

			<div class="mb-2 flex gap-1">
				{#each targetTypes as type}
					<button
						class="h-7 rounded-md px-2 text-xs transition-colors
						{form.type === type
							? 'bg-gray-200/60 text-gray-900 dark:bg-white/10 dark:text-white'
							: 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-300'}"
						onclick={() => (form.type = type)}
					>
						{type === 'webhook' ? $t('general.webhook') : $t('general.bot')}
					</button>
				{/each}
			</div>

			<label class="text-[0.625rem] text-gray-400 dark:text-gray-600">
				{$t('general.targetIdForNotify')}
			</label>
			<input
				bind:value={form.id}
				placeholder={$t('general.targetId')}
				autofocus
				autocomplete="off"
				spellcheck="false"
				class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
			/>

			{#if form.type === 'webhook'}
				<label class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2">
					{$t('general.webhook')}
				</label>
				<input
					type="url"
					bind:value={form.url}
					placeholder={editingId
						? $t('general.keepWebhookUrl')
						: 'https://hooks.slack.com/services/...'}
					autocomplete="off"
					spellcheck="false"
					class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
				/>
			{:else}
				<label class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2">
					{$t('general.bot')}
				</label>
				<select
					bind:value={form.bot_id}
					class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none py-0.5 cursor-pointer"
				>
					{#each botOptions as bot}
						<option value={bot.id}>
							{bot.name} ({bot.platform}){bot.is_running ? '' : ' - stopped'}
						</option>
					{/each}
				</select>

				<label class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2">
					{$t('general.platformDestinationId')}
				</label>
				<input
					bind:value={form.destination_chat_id}
					placeholder={$t('general.platformDestinationId')}
					autocomplete="off"
					spellcheck="false"
					class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
				/>
			{/if}

			<label class="block text-[0.625rem] text-gray-400 dark:text-gray-600 mt-3 mb-1">
				{$t('general.automaticEvents')}
			</label>
			<div class="flex flex-wrap gap-1">
				{#each eventOptions as event}
					<button
						class="h-6 rounded-md px-2 text-[0.6875rem] transition-colors
						{form.events.includes(event.event)
							? 'bg-gray-200/60 text-gray-900 dark:bg-white/10 dark:text-white'
							: 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-300'}"
						onclick={() => toggleFormEvent(event.event)}
					>
						{event.label}
					</button>
				{/each}
			</div>

			{#if form.events.length}
				<label class="block text-[0.625rem] text-gray-400 dark:text-gray-600 mt-3 mb-1">
					{$t('general.automaticDelivery')}
				</label>
				<div class="flex gap-1">
					{#each ['away', 'always'] as mode}
						<button
							class="h-6 rounded-md px-2 text-[0.6875rem] transition-colors
							{form.delivery === mode
								? 'bg-gray-200/60 text-gray-900 dark:bg-white/10 dark:text-white'
								: 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-300'}"
							onclick={() => (form.delivery = mode as NotificationDelivery)}
						>
							{mode === 'away' ? $t('general.onlyWhenAway') : $t('general.always')}
						</button>
					{/each}
				</div>
			{/if}

			<p class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-3">
				{$t('general.notifyToolAlwaysSends')}
			</p>

			<div class="flex justify-end gap-3 mt-4">
				<button
					class="text-[0.8125rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={() => (formOpen = false)}
				>
					{$t('common.cancel')}
				</button>
				<button
					class="text-[0.8125rem] text-gray-700 hover:text-gray-900 disabled:opacity-30 dark:text-gray-300 dark:hover:text-white transition-colors duration-100"
					onclick={saveTarget}
					disabled={savingTarget || (form.type === 'bot' && !botOptions.length)}
				>
					{savingTarget ? $t('settings.saving') : $t('settings.save')}
				</button>
			</div>
		</div>
	</Modal>
{/if}

<style>
	@reference "../../../app.css";

	.notification-target-row {
		@apply flex items-center gap-3 px-1 py-1.5;
		border-bottom: 1px solid var(--app-divider);
	}

	.notification-target-row:last-child {
		border-bottom: none;
	}
</style>
