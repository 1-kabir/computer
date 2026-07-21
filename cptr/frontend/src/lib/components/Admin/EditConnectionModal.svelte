<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Modal from '../Modal.svelte';
	import {
		updateConnection,
		deleteConnection,
		verifyConnection,
		type Connection
	} from '$lib/apis/admin';
	import { ApiError } from '$lib/apis';
	import { refreshChatState } from '$lib/stores/chat';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';

	interface Props {
		connection: Connection;
		onclose: () => void;
		onchanged: () => void;
	}

	let { connection, onclose, onchanged }: Props = $props();

	let formName = $state(connection.name || '');
	let formProvider = $state<'anthropic' | 'openai'>(
		connection.provider === 'openai' ? 'openai' : 'anthropic'
	);
	let formApiType = $state<'chat_completions' | 'responses'>(
		connection.api_type === 'responses' ? 'responses' : 'chat_completions'
	);
	let formProviderType = $state<'default' | 'llama.cpp'>(
		connection.provider_type === 'llama.cpp' ? 'llama.cpp' : 'default'
	);
	let formBaseUrl = $state(connection.base_url || '');
	let formApiKey = $state('');
	let formPrefixId = $state(connection.prefix_id || '');
	let formModels = $state(connection.data?.models?.join(', ') || '');
	let saving = $state(false);
	let verifying = $state(false);

	async function save() {
		if (!formBaseUrl.trim()) {
			toast.error($t('connections.fieldsRequired'));
			return;
		}
		saving = true;
		try {
			const models = formModels.trim()
				? formModels
						.split(',')
						.map((m) => m.trim())
						.filter(Boolean)
				: null;
			const name =
				formName.trim() ||
				(() => {
					try {
						return new URL(formBaseUrl.trim()).hostname;
					} catch {
						return formProvider;
					}
				})();

			const updates: Record<string, unknown> = {
				name,
				provider: formProvider,
				api_type: formProvider === 'openai' ? formApiType : 'chat_completions',
				provider_type: formProvider === 'openai' ? formProviderType : 'default',
				prefix_id: formPrefixId.trim() || null,
				base_url: formBaseUrl.trim(),
				models
			};
			if (formApiKey.trim()) {
				updates.api_key = formApiKey.trim();
			}

			await updateConnection(connection.id, updates as any);
			toast.success($t('connections.updated') || 'Connection updated');
			refreshChatState();
			onchanged();
		} catch (e) {
			toast.error(e instanceof ApiError ? e.message : $t('connections.addError') || 'Failed');
		} finally {
			saving = false;
		}
	}

	async function handleDelete() {
		try {
			await deleteConnection(connection.id);
			toast.success($t('connections.deleted'));
			refreshChatState();
			onchanged();
		} catch (e) {
			toast.error(e instanceof ApiError ? e.message : $t('connections.deleteError'));
		}
	}

	async function handleVerify() {
		verifying = true;
		try {
			const res = await verifyConnection(connection.id);
			if (res.ok) toast.success(res.message || $t('connections.verified'));
			else toast.error(res.message || $t('connections.failed'));
		} catch (e: any) {
			toast.error(e.message || $t('connections.failed'));
		} finally {
			verifying = false;
		}
	}
</script>

<Modal {onclose} class="w-full max-w-md mx-4">
	<div class="p-4">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-3">
			{$t('connections.edit') || 'Edit Connection'}
		</h2>

		<div class="flex gap-3">
			<div class="flex-1">
				<label class="text-[0.625rem] text-gray-400 dark:text-gray-600"
					>{$t('connections.name')}</label
				>
				<input
					type="text"
					placeholder={$t('connections.optional')}
					bind:value={formName}
					autofocus
					autocomplete="off"
					spellcheck="false"
					class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5"
				/>
			</div>
			<div class="w-28 shrink-0">
				<label class="text-[0.625rem] text-gray-400 dark:text-gray-600">
					{$t('connections.provider')}
				</label>
				<select
					bind:value={formProvider}
					class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none py-0.5 cursor-pointer"
				>
					<option value="openai">OpenAI</option>
					<option value="anthropic">Anthropic</option>
				</select>
			</div>
		</div>

		{#if formProvider === 'openai'}
			<label class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2">
				{$t('connections.apiType')}
			</label>
			<select
				bind:value={formApiType}
				class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none py-0.5 cursor-pointer"
			>
				<option value="chat_completions">{$t('connections.chatCompletions')}</option>
				<option value="responses">{$t('connections.responses')}</option>
			</select>

			<label for="edit-provider-type" class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2">
				{$t('connections.providerType')}
			</label>
			<select
				id="edit-provider-type"
				bind:value={formProviderType}
				class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none py-0.5 cursor-pointer"
			>
				<option value="default">{$t('general.default')}</option>
				<option value="llama.cpp">llama.cpp</option>
			</select>
		{/if}

		<label class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
			>{$t('connections.baseUrl')}</label
		>
		<input
			type="text"
			placeholder="https://api.openai.com/v1"
			bind:value={formBaseUrl}
			autocomplete="off"
			spellcheck="false"
			list="base-url-suggestions-edit"
			class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
		/>
		<datalist id="base-url-suggestions-edit">
			<option value="https://api.anthropic.com/v1" />
			<option value="https://api.openai.com/v1" />
			<option value="https://openrouter.ai/api/v1" />
			<option value="http://localhost:11434/v1" />
		</datalist>

		<label class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
			>{$t('connections.apiKey')}</label
		>
		<input
			type="password"
			placeholder={$t('connections.leaveBlankPlaceholder')}
			bind:value={formApiKey}
			autocomplete="new-password"
			class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
		/>

		<label class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
			>{$t('connections.prefixId')}</label
		>
		<input
			type="text"
			placeholder={$t('connections.prefixPlaceholder')}
			bind:value={formPrefixId}
			autocomplete="off"
			spellcheck="false"
			class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
		/>

		<label class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
			>{$t('connections.models')}</label
		>
		<p class="text-[0.625rem] text-gray-300 dark:text-gray-700 mb-0.5">
			{$t('connections.modelsHint')}
		</p>
		<input
			type="text"
			placeholder="claude-sonnet-4-20250514, claude-opus-4-20250514"
			bind:value={formModels}
			autocomplete="off"
			spellcheck="false"
			class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
		/>

		<div class="flex items-center justify-between mt-3">
			<div class="flex items-center gap-3">
				<button
					class="text-[0.8125rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={handleDelete}>{$t('admin.delete') || 'Delete'}</button
				>
				<button
					class="text-[0.8125rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 disabled:opacity-30"
					disabled={verifying}
					onclick={handleVerify}
				>
					{#if verifying}
						<Spinner size={12} />
					{:else}
						{$t('connections.verify') || 'Verify'}
					{/if}
				</button>
			</div>
			<button
				disabled={saving || !formBaseUrl.trim()}
				onclick={save}
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-30 disabled:pointer-events-none"
			>
				{#if saving}
					<Spinner size={14} />
				{:else}
					{$t('settings.save') || 'Save'} →
				{/if}
			</button>
		</div>
	</div>
</Modal>
