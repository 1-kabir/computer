<script lang="ts">
	/**
	 * Create / Edit automation modal.
	 * Uses shared ModelSelector and DropdownMenu components.
	 */
	import Modal from '../Modal.svelte';
	import Icon from '../Icon.svelte';
	import DropdownMenu from '../DropdownMenu.svelte';
	import ModelSelector from '../common/ModelSelector.svelte';
	import ScheduleDropdown from './ScheduleDropdown.svelte';
	import { workspaceList } from '$lib/stores';
	import { defaultModel } from '$lib/stores/chat';
	import { getPathDisplayName } from '$lib/utils/paths';
	import type { AutomationData, AutomationForm } from '$lib/apis/automations';
	import { createAutomation, updateAutomation } from '$lib/apis/automations';
	import { toast } from 'svelte-sonner';
	import { t } from '$lib/i18n';

	interface Props {
		automation?: AutomationData | null;
		onclose: () => void;
		onsave: (automation: AutomationData) => void;
	}

	let { automation = null, onclose, onsave }: Props = $props();

	let name = $state(automation?.name || '');
	let prompt = $state(automation?.prompt || '');
	let modelId = $state(automation?.model_id || $defaultModel || '');
	let workspace = $state(automation?.workspace || '');
	let rrule = $state(automation?.rrule || 'RRULE:FREQ=DAILY;BYHOUR=9;BYMINUTE=0');
	let saving = $state(false);

	// Workspaces
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

	async function handleSubmit() {
		if (!name.trim() || !prompt.trim() || !modelId || !workspace) return;

		saving = true;
		try {
			const form: AutomationForm = {
				name: name.trim(),
				prompt: prompt.trim(),
				model_id: modelId,
				workspace,
				rrule,
				is_active: true
			};

			let result: AutomationData;
			if (automation) {
				result = await updateAutomation(automation.id, form);
			} else {
				result = await createAutomation(form);
			}
			onsave(result);
			onclose();
		} catch (e: any) {
			toast.error(e.message || $t('automationModal.failedToSave'));
		} finally {
			saving = false;
		}
	}
</script>

<Modal {onclose} class="w-full max-w-2xl">
	<div>
		<!-- Title input -->
		<div class="px-5 pt-4 pb-2 flex items-center">
			<input
				class="w-full text-lg bg-transparent outline-none placeholder:text-gray-300 dark:placeholder:text-gray-700 text-gray-900 dark:text-white"
				type="text"
				bind:value={name}
				placeholder={$t('automationModal.titlePlaceholder')}
			/>
			<button
				class="shrink-0 ml-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
				onclick={onclose}
			>
				<Icon name="xmark" size={18} />
			</button>
		</div>

		<!-- Prompt -->
		<div class="px-5 pb-2">
			<div class="mb-1 text-[0.6875rem] text-gray-400 dark:text-gray-500">
				{$t('automationModal.instructions')}
			</div>
			<textarea
				class="w-full text-xs bg-transparent outline-none placeholder:text-gray-300 dark:placeholder:text-gray-700 text-gray-700 dark:text-gray-300 resize-none"
				bind:value={prompt}
				rows={8}
				style="min-height: 12rem;"
				placeholder={$t('automationModal.promptPlaceholder')}
			></textarea>
		</div>

		<!-- Bottom toolbar -->
		<div class="flex items-center justify-between px-4 pb-3.5 pt-1 gap-2">
			<div class="flex items-center gap-1 flex-wrap flex-1 min-w-0">
				<!-- Schedule -->
				<ScheduleDropdown bind:rrule />

				<!-- Model selector (shared component) -->
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

			<div class="flex items-center gap-2 shrink-0">
				<button
					class="px-3 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
					type="button"
					onclick={onclose}
				>
					{$t('automationModal.cancel')}
				</button>
				<button
					class="px-3.5 py-1.5 text-xs bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-white/90 transition-colors rounded-full disabled:opacity-50"
					type="button"
					onclick={handleSubmit}
					disabled={saving || !name.trim() || !prompt.trim() || !modelId || !workspace}
				>
					{saving
						? $t('automationModal.saving')
						: automation
							? $t('automationModal.save')
							: $t('automationModal.createBtn')}
				</button>
			</div>
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
