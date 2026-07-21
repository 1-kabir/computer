<script lang="ts">
	import type { SkillInfo } from '$lib/apis/skills';
	import { t } from '$lib/i18n';
	import Modal from '../Modal.svelte';
	import Icon from '../Icon.svelte';

	interface Props {
		onclose: () => void;
		skills?: SkillInfo[];
	}

	let { onclose, skills = [] }: Props = $props();
</script>

<Modal
	{onclose}
	class="mx-4 flex h-[min(42rem,80dvh)] w-full max-w-[min(44rem,calc(100vw-2rem))] flex-col overflow-hidden max-sm:mx-0 max-sm:h-[100dvh] max-sm:max-w-none max-sm:rounded-none"
>
	<div class="flex h-9 shrink-0 items-center gap-3 border-b border-gray-100 px-3 dark:border-white/8">
		<button
			type="button"
			class="flex h-7 shrink-0 items-center gap-1.5 rounded-lg pr-2 text-xs text-gray-400 transition-colors duration-75 hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
			onclick={onclose}
		>
			<Icon name="chevron-left" size={12} />
			<span>{$t('common.close')}</span>
		</button>
		<div class="min-w-0 flex-1 truncate text-xs font-medium text-gray-900 dark:text-white">
			{$t('chat.skills')}
		</div>
	</div>

	<div class="min-h-0 flex-1 overflow-y-auto p-3 text-xs">
		{#if skills.length}
			<div class="space-y-1">
				{#each skills as item}
					<div class="rounded-xl px-2 py-1.5">
						<div class="flex items-center gap-2">
							<div class="min-w-0 flex-1 truncate font-mono text-[0.75rem] text-gray-900 dark:text-white">
								{item.name}
							</div>
							<div class="shrink-0 text-[0.625rem] text-gray-400 dark:text-gray-600">
								{item.managed ? $t('chat.skillManaged') : $t('chat.skillReadOnly')}
							</div>
						</div>
						<div class="mt-0.5 line-clamp-2 text-[0.6875rem] leading-relaxed text-gray-500 dark:text-gray-400">
							{item.description}
						</div>
					</div>
				{/each}
			</div>
		{:else}
			<div class="py-8 text-center text-gray-400 dark:text-gray-600">
				{$t('chat.noSkillsFound')}
			</div>
		{/if}
	</div>
</Modal>
