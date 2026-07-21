<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Modal from '../Modal.svelte';
	import { createConnection } from '$lib/apis/admin';
	import { ApiError } from '$lib/apis';
	import { refreshChatState } from '$lib/stores/chat';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';

	interface Props {
		onclose: () => void;
		oncreated: () => void;
	}

	let { onclose, oncreated }: Props = $props();

	let formName = $state('');
	let formProvider = $state<'openai' | 'anthropic'>('openai');
	let formApiType = $state<'chat_completions' | 'responses'>('chat_completions');
	let formProviderType = $state<'default' | 'llama.cpp'>('default');
	let formBaseUrl = $state('');
	let formApiKey = $state('');
	let formPrefixId = $state('');
	let formModels = $state('');
	let creating = $state(false);

	async function create() {
		if (!formBaseUrl.trim() || !formApiKey.trim()) {
			toast.error($t('connections.fieldsRequired'));
			return;
		}
		creating = true;
		try {
			const models = formModels.trim()
				? formModels
						.split(',')
						.map((m) => m.trim())
						.filter(Boolean)
				: undefined;
			const name =
				formName.trim() ||
				(() => {
					try {
						return new URL(formBaseUrl.trim()).hostname;
					} catch {
						return formProvider;
					}
				})();
			await createConnection({
				name,
				provider: formProvider,
				api_type: formProvider === 'openai' ? formApiType : 'chat_completions',
				provider_type: formProvider === 'openai' ? formProviderType : 'default',
				prefix_id: formPrefixId.trim() || null,
				base_url: formBaseUrl.trim(),
				api_key: formApiKey.trim(),
				models
			});
			toast.success($t('connections.added'));
			refreshChatState();
			oncreated();
		} catch (e) {
			toast.error(e instanceof ApiError ? e.message : $t('connections.addError') || 'Failed');
		} finally {
			creating = false;
		}
	}
</script>

<Modal {onclose} class="w-full max-w-md mx-4">
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="p-4"
		onkeydown={(e) => {
			if (e.key === 'Enter' && formBaseUrl.trim() && formApiKey.trim()) create();
		}}
	>
		<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-3">{$t('connections.add')}</h2>

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

			<label
				for="create-provider-type"
				class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
			>
				{$t('connections.providerType')}
			</label>
			<select
				id="create-provider-type"
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
			list="base-url-suggestions"
			class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
		/>
		<datalist id="base-url-suggestions">
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
			placeholder="sk-..."
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

		<div class="flex justify-end mt-3">
			<button
				disabled={creating || !formBaseUrl.trim() || !formApiKey.trim()}
				onclick={create}
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-30 disabled:pointer-events-none"
			>
				{#if creating}
					<Spinner size={14} />
				{:else}
					{$t('connections.add')} →
				{/if}
			</button>
		</div>
	</div>
</Modal>
