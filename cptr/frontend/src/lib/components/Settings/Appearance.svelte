<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Icon from '../Icon.svelte';
	import { t } from '$lib/i18n';
	import {
		borderContrast,
		expandToolDetails,
		textScale,
		theme,
		themeConfig,
		widescreenMode
	} from '$lib/stores';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import type { Theme, ThemeConfig } from '$lib/stores';
	import {
		DEFAULT_BORDER_CONTRAST,
		MAX_BORDER_CONTRAST,
		normalizeBorderContrast,
		normalizeHexColor,
		resolveThemeMode,
		resolveThemeConfig,
		sanitizeThemeConfig
	} from '$lib/utils/appearance';

	const minTextScale = 1;
	const maxTextScale = 1.5;
	const borderContrastStep = 0.5;

	let fileInput: HTMLInputElement;
	let scaleEnabled = $state(false);
	let scaleDraft = $state(1);
	let borderContrastEnabled = $state(false);
	let borderContrastDraft = $state(DEFAULT_BORDER_CONTRAST);
	let colorDrafts = $state({ background: '', foreground: '' });

	const resolvedTheme = $derived(resolveThemeMode($theme));
	const resolvedConfig = $derived(resolveThemeConfig($theme, $themeConfig));
	const hasCustomAppearance = $derived(
		Boolean($themeConfig || $textScale !== null || $borderContrast !== null || $widescreenMode)
	);

	$effect(() => {
		colorDrafts = {
			background: resolvedConfig.background,
			foreground: resolvedConfig.foreground
		};
		if ($textScale !== null) {
			scaleEnabled = true;
			scaleDraft = $textScale;
		} else if (!scaleEnabled) {
			scaleDraft = 1;
		}
		if ($borderContrast !== null) {
			borderContrastEnabled = true;
			borderContrastDraft = $borderContrast;
		} else if (!borderContrastEnabled) {
			borderContrastDraft = DEFAULT_BORDER_CONTRAST;
		}
	});

	function setTheme(v: Theme) {
		theme.set(v);
	}

	function updateThemeColors(next: { background?: string; foreground?: string }) {
		const current = $themeConfig ?? {};
		themeConfig.set(
			sanitizeThemeConfig({
				...current,
				[resolvedTheme]: { ...(current[resolvedTheme] ?? {}), ...next }
			})
		);
	}

	function updateThemeConfig(next: ThemeConfig) {
		themeConfig.set(sanitizeThemeConfig({ ...($themeConfig ?? {}), ...next }));
	}

	function updateColor(key: 'background' | 'foreground', value: string) {
		colorDrafts = { ...colorDrafts, [key]: value };
		const color = normalizeHexColor(value);
		if (color) updateThemeColors({ [key]: color });
	}

	function updateFont(value: string) {
		updateThemeConfig({ uiFont: value });
	}

	function toggleTextScale() {
		if (scaleEnabled) {
			scaleEnabled = false;
			scaleDraft = 1;
			textScale.set(null);
		} else {
			scaleEnabled = true;
			scaleDraft = $textScale ?? 1;
		}
	}

	function toggleBorderContrast() {
		if (borderContrastEnabled) {
			borderContrastEnabled = false;
			borderContrastDraft = DEFAULT_BORDER_CONTRAST;
			borderContrast.set(null);
		} else {
			borderContrastEnabled = true;
			borderContrastDraft = $borderContrast ?? DEFAULT_BORDER_CONTRAST;
		}
	}

	function normalizeTextScale(scale: number | string) {
		const value = Number(scale);
		if (!Number.isFinite(value)) return minTextScale;
		return Math.max(minTextScale, Math.min(maxTextScale, Number(value.toFixed(2))));
	}

	function scaleLabel(scale: number) {
		return `${scale.toFixed(scale % 1 === 0 ? 0 : 2)}x`;
	}

	function borderContrastLabel(contrast: number | null) {
		if (contrast === null) return $t('general.default');
		return `${contrast.toFixed(contrast % 1 === 0 ? 0 : 1)}%`;
	}

	function setTextScalePreference(scale: number | string) {
		const next = normalizeTextScale(scale);
		scaleDraft = next;
		if (next === minTextScale) {
			scaleEnabled = false;
			textScale.set(null);
		} else {
			scaleEnabled = true;
			textScale.set(next);
		}
	}

	function setBorderContrastPreference(contrast: number | string) {
		const next = normalizeBorderContrast(contrast) ?? DEFAULT_BORDER_CONTRAST;
		borderContrastDraft = next;
		if (next === DEFAULT_BORDER_CONTRAST) {
			borderContrastEnabled = false;
			borderContrast.set(null);
		} else {
			borderContrastEnabled = true;
			borderContrast.set(next);
		}
	}

	function resetAppearance() {
		themeConfig.set(null);
		scaleEnabled = false;
		scaleDraft = 1;
		textScale.set(null);
		borderContrastEnabled = false;
		borderContrastDraft = DEFAULT_BORDER_CONTRAST;
		borderContrast.set(null);
		widescreenMode.set(false);
	}

	function exportTheme() {
		const payload = {
			theme: $theme,
			themeConfig: sanitizeThemeConfig($themeConfig),
			textScale: $textScale,
			borderContrast: $borderContrast,
			widescreenMode: $widescreenMode
		};

		try {
			const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = 'computer-theme.json';
			link.click();
			URL.revokeObjectURL(url);
			toast.success($t('appearance.exported'));
		} catch {
			toast.error($t('appearance.exportFailed'));
		}
	}

	function validateImportedColors(source: Record<string, unknown>) {
		for (const bucket of [source, source.light, source.dark]) {
			if (!bucket || typeof bucket !== 'object') continue;
			for (const key of ['background', 'foreground']) {
				if (key in bucket && !normalizeHexColor((bucket as Record<string, unknown>)[key])) {
					throw new Error('invalid color');
				}
			}
		}
	}

	async function importTheme(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (!file) return;

		try {
			const parsed = JSON.parse(await file.text());
			const source = parsed?.themeConfig ?? parsed;
			if (!source || typeof source !== 'object') throw new Error('invalid theme');
			validateImportedColors(source);
			const importedConfig = sanitizeThemeConfig(source);
			const importedTheme = ['system', 'light', 'dark'].includes(parsed?.theme)
				? (parsed.theme as Theme)
				: null;
			const importedScale =
				typeof parsed?.textScale === 'number' && Number.isFinite(parsed.textScale)
					? normalizeTextScale(parsed.textScale)
					: undefined;
			const importedWidescreenMode =
				typeof parsed?.widescreenMode === 'boolean' ? parsed.widescreenMode : undefined;
			const importedBorderContrast =
				normalizeBorderContrast(parsed?.borderContrast) ??
				(parsed?.highContrastBorders === true ? 12 : undefined);

			if (
				!importedConfig &&
				!importedTheme &&
				importedScale === undefined &&
				importedBorderContrast === undefined &&
				importedWidescreenMode === undefined
			) {
				throw new Error('empty theme');
			}
			if (importedTheme) theme.set(importedTheme);
			if (importedConfig) themeConfig.set(importedConfig);
			if (importedScale !== undefined) textScale.set(importedScale === 1 ? null : importedScale);
			if (importedBorderContrast !== undefined)
				borderContrast.set(
					importedBorderContrast === DEFAULT_BORDER_CONTRAST ? null : importedBorderContrast
				);
			if (importedWidescreenMode !== undefined) widescreenMode.set(importedWidescreenMode);
			toast.success($t('appearance.imported'));
		} catch {
			toast.error($t('appearance.importFailed'));
		}
	}
</script>

<div class="flex flex-col h-full">
	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-none pr-1.5 -mr-1.5">
		<div class="flex items-center justify-between mb-4">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('appearance.title')}</h2>
			<div class="flex items-center gap-2">
				<input
					bind:this={fileInput}
					type="file"
					accept="application/json,.json"
					class="hidden"
					onchange={importTheme}
				/>
				<button
					type="button"
					class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={() => fileInput?.click()}
				>
					{$t('appearance.import')}
				</button>
				<button
					type="button"
					class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={exportTheme}
				>
					{$t('appearance.exportTheme')}
				</button>
				<button
					type="button"
					class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 disabled:opacity-30 disabled:pointer-events-none"
					disabled={!hasCustomAppearance}
					onclick={resetAppearance}
				>
					{$t('appearance.reset')}
				</button>
			</div>
		</div>

		<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2">{$t('general.theme')}</h3>
		<div class="flex gap-1">
			{#each [{ value: 'light' as Theme, label: $t('general.light'), icon: 'sun-light' }, { value: 'dark' as Theme, label: $t('general.dark'), icon: 'half-moon' }, { value: 'system' as Theme, label: $t('general.system'), icon: 'monitor' }] as opt}
				<button
					class="flex items-center gap-1.5 h-7 px-2.5 rounded-lg text-xs transition-colors duration-100
					{$theme === opt.value
						? 'bg-gray-200/50 dark:bg-white/8 text-gray-900 dark:text-white font-medium'
						: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
					onclick={() => setTheme(opt.value)}
				>
					<Icon name={opt.icon} size={13} />
					{opt.label}
				</button>
			{/each}
		</div>

		<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
			{$t('appearance.colors')}
		</h3>
		<div class="flex flex-col gap-2.5">
			{#each [{ key: 'background' as const, label: $t('appearance.background'), value: resolvedConfig.background }, { key: 'foreground' as const, label: $t('appearance.foreground'), value: resolvedConfig.foreground }] as opt}
				<label class="flex items-center justify-between gap-3">
					<span class="text-xs text-gray-600 dark:text-gray-400">{opt.label}</span>
					<div class="flex items-center gap-2 min-w-0">
						<input
							type="color"
							value={opt.value}
							class="appearance-swatch"
							aria-label={opt.label}
							oninput={(e) => updateColor(opt.key, e.currentTarget.value)}
						/>
						<input
							value={colorDrafts[opt.key]}
							class="w-24 bg-transparent text-right text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none"
							aria-label={opt.label}
							oninput={(e) => updateColor(opt.key, e.currentTarget.value)}
						/>
					</div>
				</label>
			{/each}
		</div>

		<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
			{$t('appearance.interface')}
		</h3>
		<label class="flex items-center justify-between gap-3">
			<span class="text-xs text-gray-600 dark:text-gray-400">{$t('appearance.uiFont')}</span>
			<input
				value={$themeConfig?.uiFont ?? ''}
				placeholder={resolvedConfig.uiFont}
				class="w-full max-w-[15rem] bg-transparent text-right text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none"
				aria-label={$t('appearance.uiFont')}
				onchange={(e) => updateFont(e.currentTarget.value)}
			/>
		</label>

		<div class="w-full mt-3">
			<div class="flex items-center gap-2">
				<span id="border-contrast-label" class="text-xs text-gray-600 dark:text-gray-400">
					{$t('appearance.borderContrast')}
				</span>
				<button
					type="button"
					class="ml-auto h-6 px-2 rounded-lg text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
					aria-live="polite"
					onclick={toggleBorderContrast}
				>
					{borderContrastEnabled ? borderContrastLabel(borderContrastDraft) : $t('general.default')}
				</button>
			</div>
			{#if borderContrastEnabled}
				<div class="flex items-center gap-1.5 pt-1.5">
					<button
						type="button"
						class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
						aria-labelledby="border-contrast-label"
						aria-label={$t('appearance.decreaseBorderContrast')}
						onclick={() => setBorderContrastPreference(borderContrastDraft - borderContrastStep)}
					>
						<Icon name="minus" size={12} />
					</button>
					<input
						id="border-contrast-slider"
						class="appearance-range flex-1 min-w-0"
						type="range"
						min={DEFAULT_BORDER_CONTRAST}
						max={MAX_BORDER_CONTRAST}
						step={borderContrastStep}
						bind:value={borderContrastDraft}
						aria-labelledby="border-contrast-label"
						aria-valuemin={DEFAULT_BORDER_CONTRAST}
						aria-valuemax={MAX_BORDER_CONTRAST}
						aria-valuenow={borderContrastDraft}
						aria-valuetext={borderContrastLabel(borderContrastDraft)}
						oninput={() => setBorderContrastPreference(borderContrastDraft)}
					/>
					<button
						type="button"
						class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
						aria-labelledby="border-contrast-label"
						aria-label={$t('appearance.increaseBorderContrast')}
						onclick={() => setBorderContrastPreference(borderContrastDraft + borderContrastStep)}
					>
						<Icon name="plus" size={12} />
					</button>
				</div>
			{/if}
		</div>

		<label class="flex items-center justify-between gap-3 mt-3">
			<span class="text-xs text-gray-600 dark:text-gray-400">{$t('appearance.widescreenMode')}</span
			>
			<ToggleSwitch value={$widescreenMode} onchange={(value) => widescreenMode.set(value)} />
		</label>

		<label class="flex items-center justify-between gap-3 mt-3">
			<span class="text-xs text-gray-600 dark:text-gray-400"
				>{$t('appearance.expandToolDetails')}</span
			>
			<ToggleSwitch value={$expandToolDetails} onchange={(value) => expandToolDetails.set(value)} />
		</label>

		<div class="w-full mt-5">
			<div class="flex items-center gap-2">
				<span id="ui-scale-label" class="text-xs text-gray-600 dark:text-gray-400">
					{$t('general.uiScale')}
				</span>
				<button
					type="button"
					class="ml-auto h-6 px-2 rounded-lg text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
					aria-live="polite"
					onclick={toggleTextScale}
				>
					{scaleEnabled ? scaleLabel(scaleDraft) : $t('general.default')}
				</button>
			</div>

			{#if scaleEnabled}
				<div class="flex items-center gap-1.5 pt-1.5">
					<button
						type="button"
						class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
						aria-labelledby="ui-scale-label"
						aria-label={$t('general.decreaseUiScale')}
						onclick={() => setTextScalePreference(scaleDraft - 0.1)}
					>
						<Icon name="minus" size={12} />
					</button>
					<input
						id="ui-scale-slider"
						class="appearance-range flex-1 min-w-0"
						type="range"
						min={minTextScale}
						max={maxTextScale}
						step="0.01"
						bind:value={scaleDraft}
						aria-labelledby="ui-scale-label"
						aria-valuemin={minTextScale}
						aria-valuemax={maxTextScale}
						aria-valuenow={scaleDraft}
						aria-valuetext={scaleLabel(scaleDraft)}
						oninput={() => setTextScalePreference(scaleDraft)}
					/>
					<button
						type="button"
						class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
						aria-labelledby="ui-scale-label"
						aria-label={$t('general.increaseUiScale')}
						onclick={() => setTextScalePreference(scaleDraft + 0.1)}
					>
						<Icon name="plus" size={12} />
					</button>
				</div>
			{/if}
		</div>
	</div>
</div>

<style>
	.appearance-swatch {
		width: 1.5rem;
		height: 1.5rem;
		border: 1px solid var(--app-border);
		border-radius: 624.9375rem;
		background: transparent;
		padding: 0.125rem;
	}

	.appearance-range {
		appearance: none;
		height: 1rem;
		background: transparent;
		cursor: pointer;
	}

	.appearance-range::-webkit-slider-runnable-track {
		height: 0.1875rem;
		border-radius: 624.9375rem;
		background: color-mix(in oklab, var(--app-fg) 24%, transparent);
	}

	.appearance-range::-webkit-slider-thumb {
		appearance: none;
		width: 0.75rem;
		height: 0.75rem;
		margin-top: -0.3125rem;
		border-radius: 624.9375rem;
		border: 1px solid color-mix(in oklab, var(--app-bg) 70%, transparent);
		background: color-mix(in oklab, var(--app-fg) 82%, var(--app-bg));
	}

	.appearance-range::-moz-range-track {
		height: 0.1875rem;
		border-radius: 624.9375rem;
		background: color-mix(in oklab, var(--app-fg) 24%, transparent);
	}

	.appearance-range::-moz-range-thumb {
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 624.9375rem;
		border: 1px solid color-mix(in oklab, var(--app-bg) 70%, transparent);
		background: color-mix(in oklab, var(--app-fg) 82%, var(--app-bg));
	}
</style>
