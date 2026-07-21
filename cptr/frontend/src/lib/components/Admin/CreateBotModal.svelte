<script lang="ts">
	/**
	 * Create / Edit bot modal.
	 * Follows the same pattern as CreateConnectionModal and AutomationModal.
	 */
	import Modal from '../Modal.svelte';
	import Icon from '../Icon.svelte';
	import DropdownMenu from '../DropdownMenu.svelte';
	import ModelSelector from '../common/ModelSelector.svelte';
	import { workspaceList } from '$lib/stores';
	import { defaultModel } from '$lib/stores/chat';
	import { getPathDisplayName } from '$lib/utils/paths';
	import { createBot, updateBot, verifyBotToken, type BotData, type BotForm } from '$lib/apis/bots';
	import { toast } from 'svelte-sonner';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';

	interface Props {
		bot?: BotData | null;
		onclose: () => void;
		onsave: () => void;
	}

	let { bot = null, onclose, onsave }: Props = $props();

	let platform = $state(bot?.platform || 'telegram');
	let name = $state(bot?.name || '');
	let token = $state('');
	let modelId = $state(bot?.model_id || $defaultModel || '');
	let workspace = $state(bot?.workspace || '');
	let allowedSenders = $state(bot?.allowed_senders?.join(', ') || '');
	let saving = $state(false);

	// Token verification
	let verifying = $state(false);
	let verifyResult = $state<{ ok: boolean; info?: any; error?: string } | null>(null);

	// Workspace dropdown
	let showWsMenu = $state(false);
	let workspaceButtonEl: HTMLButtonElement | undefined = $state();

	$effect(() => {
		if (!workspace && $workspaceList.length > 0) {
			workspace = $workspaceList[0].path;
		}
	});

	let workspaceMenuItems = $derived(
		$workspaceList.map((workspaceOption) => ({
			label: workspaceOption.name,
			icon: 'folder',
			active: workspaceOption.path === workspace,
			check: true,
			onclick: () => {
				workspace = workspaceOption.path;
			}
		}))
	);

	let selectedWorkspaceName = $derived(
		$workspaceList.find((w) => w.path === workspace)?.name ||
			getPathDisplayName(workspace) ||
			$t('automationModal.selectWorkspace')
	);

	const platformHints: Record<string, string> = $derived({
		telegram: $t('messaging.hint.telegram'),
		discord: $t('messaging.hint.discord'),
		slack: $t('messaging.hint.slack'),
		whatsapp: $t('messaging.hint.whatsapp'),
		signal: $t('messaging.hint.signal')
	});

	async function handleVerify() {
		if (!token.trim()) return;
		verifying = true;
		verifyResult = null;
		try {
			verifyResult = await verifyBotToken(platform, token.trim());
		} catch (e: any) {
			verifyResult = { ok: false, error: e.message || $t('messaging.verificationFailed') };
		} finally {
			verifying = false;
		}
	}

	async function handleSubmit() {
		if (!name.trim() || !modelId || !workspace) return;
		if (!bot && !token.trim()) return;

		saving = true;
		try {
			if (bot) {
				const update: any = {
					name: name.trim(),
					model_id: modelId,
					workspace,
					allowed_senders:
						allowedSenders
							.split(',')
							.map((s) => s.trim())
							.filter(Boolean) || undefined
				};
				if (token.trim()) update.token = token.trim();
				await updateBot(bot.id, update);
			} else {
				await createBot({
					platform,
					name: name.trim(),
					token: token.trim(),
					model_id: modelId,
					workspace,
					allowed_senders:
						allowedSenders
							.split(',')
							.map((s) => s.trim())
							.filter(Boolean) || undefined
				});
			}
			onsave();
			onclose();
		} catch (e: any) {
			toast.error(e.message || $t('messaging.failedToSave'));
		} finally {
			saving = false;
		}
	}
</script>

<Modal {onclose} class="w-full max-w-md mx-4">
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="p-4"
		onkeydown={(e) => {
			if (e.key === 'Enter' && name.trim() && (bot || token.trim())) handleSubmit();
		}}
	>
		<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-3">
			{bot ? $t('messaging.editBot') : $t('messaging.addBot')}
		</h2>

		{#if !bot}
			<!-- Platform + Name on same row -->
			<div class="flex gap-3">
				<div class="flex-1">
					<label class="text-[0.625rem] text-gray-400 dark:text-gray-600"
						>{$t('messaging.name')}</label
					>
					<input
						type="text"
						bind:value={name}
						placeholder={$t('messaging.namePlaceholder')}
						autofocus
						autocomplete="off"
						spellcheck="false"
						class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5"
					/>
				</div>
				<div class="w-28 shrink-0">
					<label class="text-[0.625rem] text-gray-400 dark:text-gray-600"
						>{$t('messaging.platform')}</label
					>
					<select
						bind:value={platform}
						onchange={() => {
							verifyResult = null;
						}}
						class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none py-0.5 cursor-pointer"
					>
						<option value="telegram">Telegram</option>
						<option value="discord">Discord</option>
						<option value="slack">Slack</option>
						<option value="whatsapp">WhatsApp</option>
						<option value="signal">Signal</option>
					</select>
				</div>
			</div>
		{:else}
			<!-- Edit mode: just name -->
			<label class="text-[0.625rem] text-gray-400 dark:text-gray-600">{$t('messaging.name')}</label>
			<input
				type="text"
				bind:value={name}
				placeholder={$t('messaging.namePlaceholder')}
				autofocus
				autocomplete="off"
				spellcheck="false"
				class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5"
			/>
		{/if}

		<!-- Token -->
		<label class="text-[0.625rem] text-gray-400 dark:text-gray-600">
			{$t('messaging.token')}
			{#if bot}<span class="text-gray-300 dark:text-gray-700">({bot.token_masked})</span>{/if}
		</label>
		<div class="flex gap-2 items-center mb-0.5">
			<input
				type="password"
				bind:value={token}
				placeholder={bot
					? $t('messaging.tokenKeep')
					: platformHints[platform] || $t('messaging.tokenPaste')}
				autocomplete="new-password"
				class="flex-1 bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
			/>
			{#if !bot}
				<button
					type="button"
					class="text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors disabled:opacity-30"
					onclick={handleVerify}
					disabled={verifying || !token.trim()}
				>
					{#if verifying}
						<Spinner size={10} />
					{:else}
						{$t('messaging.verify')}
					{/if}
				</button>
			{/if}
		</div>

		{#if verifyResult}
			<p class="text-[0.625rem] mb-1 text-gray-500 dark:text-gray-400">
				{#if verifyResult.ok}
					✓ {verifyResult.info?.username
						? `@${verifyResult.info.username}`
						: `ID: ${verifyResult.info?.id}`}
				{:else}
					✗ {verifyResult.error}
				{/if}
			</p>
		{/if}

		<!-- Allowed senders -->
		<label class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-1"
			>{$t('messaging.allowedSenders')}</label
		>
		<p class="text-[0.625rem] text-gray-300 dark:text-gray-700 mb-0.5">
			{$t('messaging.allowedSendersHint')}
		</p>
		<input
			type="text"
			bind:value={allowedSenders}
			placeholder="123456789, 987654321"
			autocomplete="off"
			spellcheck="false"
			class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
		/>

		<!-- Bottom toolbar -->
		<div class="flex items-center justify-between mt-3 pt-2 gap-2">
			<div class="flex items-center gap-1 flex-1 min-w-0">
				<!-- Model selector -->
				<ModelSelector bind:selectedModel={modelId} />

				<!-- Workspace selector -->
				<button
					bind:this={workspaceButtonEl}
					type="button"
					class="flex items-center gap-1 px-2 py-1 rounded-lg text-[0.6875rem] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 transition-colors duration-100"
					onclick={() => (showWsMenu = !showWsMenu)}
				>
					<Icon name="folder" size={12} />
					<span class="truncate max-w-[7.5rem]">{selectedWorkspaceName}</span>
					<svg
						class="w-3 h-3 opacity-50"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2.5"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<polyline points="6 9 12 15 18 9" />
					</svg>
				</button>
			</div>

			<button
				type="button"
				disabled={saving || !name.trim() || (!bot && !token.trim()) || !modelId || !workspace}
				onclick={handleSubmit}
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-30 disabled:pointer-events-none"
			>
				{#if saving}
					<Spinner size={14} />
				{:else}
					{bot ? $t('messaging.save') : $t('messaging.add')}
				{/if}
			</button>
		</div>
	</div>
</Modal>

{#if showWsMenu && workspaceButtonEl}
	<DropdownMenu
		items={workspaceMenuItems}
		anchor={workspaceButtonEl}
		onclose={() => (showWsMenu = false)}
		preferAbove={true}
		maxHeight="15rem"
		className="w-48"
	/>
{/if}
