<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount } from 'svelte';
	import { getAdminConfig, updateConfig } from '$lib/apis/admin';
	import {
		clearManagedChromeProfile,
		connectPersonalChrome,
		disconnectPersonalChrome,
		getPersonalChrome,
		testBrowserCdp,
		type PersonalChromeStatus
	} from '$lib/apis/browser';
	import { t } from '$lib/i18n';
	import { tooltip } from '$lib/tooltip';
	import Collapsible from '$lib/components/Collapsible.svelte';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let loading = $state(true);
	let saving = $state(false);
	let testing = $state(false);
	let connectingPersonal = $state(false);
	let testResult = $state<{ ok: boolean; message: string } | null>(null);
	let personalStatus = $state<PersonalChromeStatus | null>(null);

	// ── Web search ────────────────────────────────────────
	let webEnabled = $state(true);
	let searchProvider = $state('auto');
	let exaKey = $state('');
	let tavilyKey = $state('');
	let braveKey = $state('');
	let perplexityKey = $state('');
	let perplexityBaseUrl = $state('');
	let firecrawlSearchKey = $state('');
	let firecrawlSearchBaseUrl = $state('https://api.firecrawl.dev');
	let searxngBaseUrl = $state('');
	let ccKey = $state('');
	let ccBaseUrl = $state('');
	let ccModel = $state('');

	// ── Browser ───────────────────────────────────────────
	let browserEnabled = $state(false);
	let browserTabDefaultMode = $state<'proxy' | 'chrome'>('proxy');
	let browserTabChromeSource = $state<'managed' | 'personal'>('managed');
	let personalKeepAlive = $state(true);
	let browserProvider = $state<'local' | 'firecrawl' | 'browser_use'>('local');
	let cdpUrl = $state('http://localhost:9222');
	let autoLaunch = $state(true);
	let sessionTimeout = $state(10);
	let firecrawlApiKey = $state('');
	let firecrawlBaseUrl = $state('https://api.firecrawl.dev');
	let browserUseApiKey = $state('');
	let browserUseBaseUrl = $state('https://api.browser-use.com');
	type BrowserEncoderHardwareAcceleration = 'no-preference' | 'prefer-hardware' | 'prefer-software';
	let browserQualityDefault = $state<'low' | 'balanced' | 'crisp'>('balanced');
	let browserEncoderHardwareAcceleration =
		$state<BrowserEncoderHardwareAcceleration>('no-preference');
	let browserQualityProfiles = $state({
		low: { bitrate: 3000000, frame_rate: 15 },
		balanced: { bitrate: 6000000, frame_rate: 24 },
		crisp: { bitrate: 12000000, frame_rate: 30 }
	});
	let browserQualityMaxResolution = $state(1080);
	let browserQualityMaxBitrate = $state(12000000);

	onMount(async () => {
		try {
			const config = await getAdminConfig();

			// Web search
			webEnabled = config['web.enabled'] !== false;
			searchProvider = config['web.search_provider'] || 'auto';
			exaKey = (config['web.exa_api_key'] as string) || '';
			tavilyKey = (config['web.tavily_api_key'] as string) || '';
			braveKey = (config['web.brave_api_key'] as string) || '';
			perplexityKey = (config['web.perplexity_api_key'] as string) || '';
			perplexityBaseUrl = (config['web.perplexity_base_url'] as string) || '';
			firecrawlSearchKey = (config['web.firecrawl_api_key'] as string) || '';
			firecrawlSearchBaseUrl =
				(config['web.firecrawl_base_url'] as string) || 'https://api.firecrawl.dev';
			searxngBaseUrl = (config['web.searxng_base_url'] as string) || '';
			ccKey = (config['web.chat_completions_api_key'] as string) || '';
			ccBaseUrl = (config['web.chat_completions_base_url'] as string) || '';
			ccModel = (config['web.chat_completions_model'] as string) || '';

			// Browser
			browserEnabled = config['browser.enabled'] === true || config['browser.enabled'] === 'true';
			browserTabDefaultMode = config['browser.tab_default_mode'] === 'chrome' ? 'chrome' : 'proxy';
			browserTabChromeSource =
				config['browser.tab_chrome_source'] === 'personal' ? 'personal' : 'managed';
			personalKeepAlive = config['browser.personal_keep_alive'] !== false;
			browserProvider = (config['browser.provider'] as typeof browserProvider) || 'local';
			cdpUrl = (config['browser.cdp_url'] as string) || 'http://localhost:9222';
			autoLaunch =
				config['browser.auto_launch'] !== false && config['browser.auto_launch'] !== 'false';
			sessionTimeout = Number(config['browser.session_timeout_minutes']) || 10;
			firecrawlApiKey = (config['browser.firecrawl_api_key'] as string) || '';
			firecrawlBaseUrl =
				(config['browser.firecrawl_base_url'] as string) || 'https://api.firecrawl.dev';
			browserUseApiKey = (config['browser.browser_use_api_key'] as string) || '';
			browserUseBaseUrl =
				(config['browser.browser_use_base_url'] as string) || 'https://api.browser-use.com';
			if (
				config['browser.quality.default'] === 'low' ||
				config['browser.quality.default'] === 'crisp'
			)
				browserQualityDefault = config['browser.quality.default'];
			const qualityProfiles = config['browser.quality.profiles'];
			if (qualityProfiles && typeof qualityProfiles === 'object') {
				for (const name of ['low', 'balanced', 'crisp'] as const) {
					const profile = (qualityProfiles as Record<string, unknown>)[name];
					if (profile && typeof profile === 'object') {
						const value = profile as Record<string, unknown>;
						browserQualityProfiles[name] = {
							bitrate: Number(value.bitrate) || browserQualityProfiles[name].bitrate,
							frame_rate:
								Number(value.frame_rate ?? value.fps) || browserQualityProfiles[name].frame_rate
						};
					}
				}
			}
			browserQualityMaxResolution = Number(config['browser.quality.max_resolution']) || 1080;
			browserQualityMaxBitrate = Number(config['browser.quality.max_bitrate']) || 12000000;
			if (
				config['browser.encoder.hardware_acceleration'] === 'prefer-hardware' ||
				config['browser.encoder.hardware_acceleration'] === 'prefer-software'
			) {
				browserEncoderHardwareAcceleration = config[
					'browser.encoder.hardware_acceleration'
				] as BrowserEncoderHardwareAcceleration;
			}
		} catch {
			toast.error($t('admin.failedToLoadConfig'));
		}
		try {
			personalStatus = await getPersonalChrome();
		} catch {
			personalStatus = null;
		}
		loading = false;
	});

	async function save() {
		saving = true;
		try {
			const cfg: Record<string, unknown> = {
				'web.enabled': webEnabled,
				'web.search_provider': searchProvider,
				'web.exa_api_key': exaKey,
				'web.tavily_api_key': tavilyKey,
				'web.brave_api_key': braveKey,
				'web.perplexity_api_key': perplexityKey,
				'web.perplexity_base_url': perplexityBaseUrl,
				'web.firecrawl_api_key': firecrawlSearchKey,
				'web.firecrawl_base_url': firecrawlSearchBaseUrl,
				'web.searxng_base_url': searxngBaseUrl,
				'web.chat_completions_api_key': ccKey,
				'web.chat_completions_base_url': ccBaseUrl,
				'web.chat_completions_model': ccModel,
				'browser.enabled': browserEnabled,
				'browser.tab_default_mode': browserTabDefaultMode,
				'browser.tab_chrome_source': browserTabChromeSource,
				'browser.personal_keep_alive': personalKeepAlive,
				'browser.provider': browserProvider,
				'browser.cdp_url': cdpUrl,
				'browser.auto_launch': autoLaunch,
				'browser.session_timeout_minutes': sessionTimeout,
				'browser.firecrawl_api_key': firecrawlApiKey,
				'browser.firecrawl_base_url': firecrawlBaseUrl,
				'browser.browser_use_api_key': browserUseApiKey,
				'browser.browser_use_base_url': browserUseBaseUrl,
				'browser.quality.default': browserQualityDefault,
				'browser.encoder.hardware_acceleration': browserEncoderHardwareAcceleration,
				'browser.quality.profiles': browserQualityProfiles,
				'browser.quality.max_resolution': browserQualityMaxResolution,
				'browser.quality.max_bitrate': browserQualityMaxBitrate
			};
			await updateConfig(cfg);
			if (
				browserTabDefaultMode !== 'chrome' ||
				browserTabChromeSource !== 'personal' ||
				(!personalKeepAlive && personalStatus?.session_count === 0)
			) {
				personalStatus = await disconnectPersonalChrome();
			}
			toast.success($t('settings.saved'));
		} catch {
			toast.error($t('admin.failedToSave'));
		} finally {
			saving = false;
		}
	}

	async function clearManagedProfile() {
		if (!confirm($t('admin.managedChromeClearConfirm'))) return;
		try {
			await clearManagedChromeProfile();
			toast.success($t('admin.managedChromeCleared'));
		} catch (error) {
			toast.error(error instanceof Error ? error.message : $t('admin.failedToSave'));
		}
	}

	async function testConnection() {
		testing = true;
		testResult = null;
		try {
			const result = await testBrowserCdp(cdpUrl);
			testResult = { ok: true, message: result.browser };
		} catch (error) {
			testResult = {
				ok: false,
				message: error instanceof Error ? error.message : 'Could not connect'
			};
		} finally {
			testing = false;
		}
	}

	async function togglePersonalChrome() {
		connectingPersonal = true;
		try {
			personalStatus =
				personalStatus?.status === 'playing'
					? await disconnectPersonalChrome()
					: await connectPersonalChrome(cdpUrl);
		} catch (error) {
			testResult = {
				ok: false,
				message: error instanceof Error ? error.message : $t('admin.personalChromeUnavailable')
			};
		} finally {
			connectingPersonal = false;
		}
	}
</script>

<div class="flex flex-col h-full">
	{#if loading}
		<div class="flex justify-center py-8"><Spinner size={16} /></div>
	{:else}
		<div class="flex-1 min-h-0 overflow-y-auto">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$t('admin.web')}</h2>

			<!-- Search -->
			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2">{$t('admin.webSearch')}</h3>

			<div class="flex flex-col gap-2.5">
				<label class="flex items-center justify-between cursor-pointer">
					<span class="text-xs text-gray-600 dark:text-gray-400">{$t('admin.webEnabled')}</span>
					<ToggleSwitch
						value={webEnabled}
						onchange={(v) => {
							webEnabled = v;
						}}
					/>
				</label>
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
					{webEnabled ? $t('admin.webEnabledHint') : $t('admin.webDisabledHint')}
				</p>

				{#if webEnabled}
					<div class="flex items-center justify-between">
						<span class="text-xs text-gray-600 dark:text-gray-400"
							>{$t('admin.webSearchProvider')}</span
						>
						<select
							bind:value={searchProvider}
							class="bg-transparent text-xs text-gray-600 dark:text-gray-400 outline-none cursor-pointer"
						>
							<option value="auto">{$t('admin.webProviderAuto')}</option>
							<option value="exa">Exa</option>
							<option value="tavily">Tavily</option>
							<option value="brave">Brave</option>
							<option value="firecrawl">{$t('admin.browserFirecrawl')}</option>
							<option value="searxng">SearXNG</option>
							<option value="perplexity">Perplexity</option>
							<option value="duckduckgo">DuckDuckGo</option>
							<option value="chat_completions">{$t('admin.webChatCompletions')}</option>
						</select>
					</div>
					<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
						{#if searchProvider === 'auto'}
							{$t('admin.webAutoHint')}
						{:else if searchProvider === 'duckduckgo'}
							{$t('admin.webDuckDuckGoNote')}
						{/if}
					</p>

					{#if searchProvider === 'exa'}
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="exa-key"
								>{$t('admin.webExaKey')}</label
							>
							<input
								id="exa-key"
								type="password"
								bind:value={exaKey}
								placeholder="exa-..."
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
							<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
								{$t('admin.webExaHint')}
							</p>
						</div>
					{:else if searchProvider === 'tavily'}
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="tavily-key"
								>{$t('admin.webTavilyKey')}</label
							>
							<input
								id="tavily-key"
								type="password"
								bind:value={tavilyKey}
								placeholder="tvly-..."
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
							<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
								{$t('admin.webTavilyHint')}
							</p>
						</div>
					{:else if searchProvider === 'brave'}
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="brave-key"
								>{$t('admin.webBraveKey')}</label
							>
							<input
								id="brave-key"
								type="password"
								bind:value={braveKey}
								placeholder="BSA..."
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
							<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
								{$t('admin.webBraveHint')}
							</p>
						</div>
					{:else if searchProvider === 'firecrawl'}
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="web-fc-key"
								>{$t('admin.webFirecrawlKey')}</label
							>
							<input
								id="web-fc-key"
								type="password"
								bind:value={firecrawlSearchKey}
								placeholder="fc-..."
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
							<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
								{$t('admin.webFirecrawlHint')}
							</p>
						</div>
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="web-fc-url"
								>{$t('admin.webFirecrawlBaseUrl')}</label
							>
							<input
								id="web-fc-url"
								type="text"
								bind:value={firecrawlSearchBaseUrl}
								placeholder="https://api.firecrawl.dev"
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
							<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
								{$t('admin.browserFirecrawlBaseUrlHint')}
							</p>
						</div>
					{:else if searchProvider === 'searxng'}
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="searxng-base-url"
								>{$t('admin.webSearxngBaseUrl')}</label
							>
							<input
								id="searxng-base-url"
								type="text"
								bind:value={searxngBaseUrl}
								placeholder="https://search.example.com"
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
							<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
								{$t('admin.webSearxngHint')}
							</p>
						</div>
					{:else if searchProvider === 'perplexity'}
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="pplx-key"
								>{$t('admin.webPerplexityKey')}</label
							>
							<input
								id="pplx-key"
								type="password"
								bind:value={perplexityKey}
								placeholder="pplx-..."
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
							<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
								{$t('admin.webPerplexityHint')}
							</p>
						</div>
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="pplx-base-url"
								>{$t('admin.webPerplexityBaseUrl')}</label
							>
							<input
								id="pplx-base-url"
								type="text"
								bind:value={perplexityBaseUrl}
								placeholder="https://api.perplexity.ai"
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
							<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
								{$t('admin.webPerplexityBaseUrlHint')}
							</p>
						</div>
					{:else if searchProvider === 'chat_completions'}
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="cc-base-url"
								>{$t('admin.webCcBaseUrl')}</label
							>
							<input
								id="cc-base-url"
								type="text"
								bind:value={ccBaseUrl}
								placeholder="https://api.perplexity.ai/v1"
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
						</div>
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="cc-key"
								>{$t('admin.webCcKey')}</label
							>
							<input
								id="cc-key"
								type="password"
								bind:value={ccKey}
								placeholder="sk-..."
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
						</div>
						<div>
							<label class="text-xs text-gray-600 dark:text-gray-400" for="cc-model"
								>{$t('admin.webCcModel')}</label
							>
							<input
								id="cc-model"
								type="text"
								bind:value={ccModel}
								placeholder="sonar-pro"
								class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
						</div>
						<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
							{$t('admin.webCcHint')}
						</p>
					{/if}
				{/if}
			</div>

			<!-- Browser -->
			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">{$t('admin.browser')}</h3>

			<div>
				<p class="mb-2 text-xs text-gray-500 dark:text-gray-500">
					{$t('admin.browserTabs')}
				</p>
				<div class="flex flex-col gap-2.5">
					<div class="flex items-center justify-between">
						<div>
							<span class="text-xs text-gray-600 dark:text-gray-400"
								>{$t('admin.browserTabDefault')}</span
							>
							<p class="text-[0.625rem] text-gray-400 dark:text-gray-600">
								{$t('admin.browserTabDefaultHint')}
							</p>
						</div>
						<select
							bind:value={browserTabDefaultMode}
							class="bg-transparent text-xs text-gray-600 dark:text-gray-400 outline-none cursor-pointer"
						>
							<option value="proxy">{$t('browser.proxy')}</option>
							<option value="chrome">{$t('browser.chrome')}</option>
						</select>
					</div>

					{#if browserTabDefaultMode === 'chrome'}
						<div class="flex items-center justify-between">
							<div>
								<span class="text-xs text-gray-600 dark:text-gray-400"
									>{$t('admin.chromeProfile')}</span
								>
								<p class="text-[0.625rem] text-gray-400 dark:text-gray-600">
									{browserTabChromeSource === 'personal'
										? $t('admin.personalChromeHint')
										: $t('admin.managedChromeHint')}
								</p>
							</div>
							<select
								bind:value={browserTabChromeSource}
								class="bg-transparent text-xs text-gray-600 dark:text-gray-400 outline-none cursor-pointer"
							>
								<option value="managed">{$t('admin.managedChrome')}</option>
								<option value="personal">{$t('admin.personalChrome')}</option>
							</select>
						</div>

						{#if browserTabChromeSource === 'managed'}
							<div class="flex items-center justify-between gap-4">
								<div>
									<span class="text-xs text-gray-600 dark:text-gray-400"
										>{$t('admin.managedChromeData')}</span
									>
									<p class="text-[0.625rem] text-gray-400 dark:text-gray-600">
										{$t('admin.managedChromeDataHint')}
									</p>
								</div>
								<button
									class="shrink-0 text-xs text-gray-500 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400"
									onclick={clearManagedProfile}>{$t('admin.managedChromeClear')}</button
								>
							</div>
							<div class="flex items-center justify-between gap-4">
								<div>
									<span class="text-xs text-gray-600 dark:text-gray-400"
										>{$t('admin.browserQuality')}</span
									>
									<p class="text-[0.625rem] text-gray-400 dark:text-gray-600">
										{$t('admin.browserQualityHintV2')}
									</p>
								</div>
								<select
									bind:value={browserQualityDefault}
									class="bg-transparent text-xs text-gray-600 dark:text-gray-400 outline-none cursor-pointer"
								>
									<option value="low">{$t('admin.browserQualityLow')}</option>
									<option value="balanced">{$t('admin.browserQualityBalanced')}</option>
									<option value="crisp">{$t('admin.browserQualityCrisp')}</option>
								</select>
							</div>
							<Collapsible title={$t('admin.advanced')}>
								<div class="flex flex-col gap-2.5">
									{#each ['low', 'balanced', 'crisp'] as name}
										<div class="flex items-center justify-between gap-4">
											<span class="text-xs capitalize text-gray-600 dark:text-gray-400">{name}</span
											>
											<div class="flex items-center gap-1.5">
												<input
													type="number"
													min="1000000"
													max="12000000"
													step="1000000"
													aria-label={`${name} ${$t('admin.browserQualityMaxBitrate')}`}
													use:tooltip={$t('admin.browserQualityMaxBitrate')}
													bind:value={
														browserQualityProfiles[name as 'low' | 'balanced' | 'crisp'].bitrate
													}
													class="h-7 w-20 rounded-lg border border-gray-200 bg-gray-100 px-2 text-xs text-gray-700 outline-none dark:border-white/8 dark:bg-white/6 dark:text-gray-300"
												/>
												<input
													type="number"
													min="1"
													max="60"
													aria-label={`${name} ${$t('admin.browserQualityFrameRate')}`}
													use:tooltip={$t('admin.browserQualityFrameRate')}
													bind:value={
														browserQualityProfiles[name as 'low' | 'balanced' | 'crisp'].frame_rate
													}
													class="h-7 w-14 rounded-lg border border-gray-200 bg-gray-100 px-2 text-xs text-gray-700 outline-none dark:border-white/8 dark:bg-white/6 dark:text-gray-300"
												/>
											</div>
										</div>
									{/each}
									<div class="flex items-center justify-between gap-4">
										<span class="text-xs text-gray-600 dark:text-gray-400"
											>{$t('admin.browserQualityMaxHeight')}</span
										>
										<input
											type="number"
											min="240"
											max="2160"
											list="browser-quality-heights"
											bind:value={browserQualityMaxResolution}
											class="h-7 w-24 rounded-lg border border-gray-200 bg-gray-100 px-2 text-xs text-gray-700 outline-none dark:border-white/8 dark:bg-white/6 dark:text-gray-300"
										/>
										<datalist id="browser-quality-heights">
											<option value="720"></option><option value="1080"></option><option
												value="1440"
											></option><option value="2160"></option>
										</datalist>
									</div>
									<div class="flex items-center justify-between gap-4">
										<span class="text-xs text-gray-600 dark:text-gray-400"
											>{$t('admin.browserQualityMaxBitrate')}</span
										>
										<input
											type="number"
											min="1000000"
											max="12000000"
											step="1000000"
											bind:value={browserQualityMaxBitrate}
											class="h-7 w-24 rounded-lg border border-gray-200 bg-gray-100 px-2 text-xs text-gray-700 outline-none dark:border-white/8 dark:bg-white/6 dark:text-gray-300"
										/>
									</div>
									<div class="flex items-center justify-between gap-4">
										<div>
											<span class="text-xs text-gray-600 dark:text-gray-400"
												>{$t('admin.browserEncoderHardwareAcceleration')}</span
											>
											<p class="text-[0.625rem] text-gray-400 dark:text-gray-600">
												{$t('admin.browserEncoderHardwareAccelerationHint')}
											</p>
										</div>
										<select
											bind:value={browserEncoderHardwareAcceleration}
											class="cursor-pointer bg-transparent text-xs text-gray-600 outline-none dark:text-gray-400"
										>
											<option value="no-preference"
												>{$t('admin.browserEncoderAccelerationAuto')}</option
											>
											<option value="prefer-hardware"
												>{$t('admin.browserEncoderAccelerationHardware')}</option
											>
											<option value="prefer-software"
												>{$t('admin.browserEncoderAccelerationSoftware')}</option
											>
										</select>
									</div>
								</div>
							</Collapsible>
						{:else}
							<div>
								<label class="text-xs text-gray-600 dark:text-gray-400" for="tab-cdp-url"
									>{$t('admin.browserCdpUrl')}</label
								>
								<div class="mt-1 flex gap-1.5">
									<input
										id="tab-cdp-url"
										type="text"
										bind:value={cdpUrl}
										placeholder="http://localhost:9222"
										class="h-7 flex-1 rounded-lg border border-gray-200 bg-gray-100 px-2 text-xs text-gray-700 outline-none transition-colors focus:border-blue-400 dark:border-white/8 dark:bg-white/6 dark:text-gray-300 dark:focus:border-blue-500"
									/>
									<button
										class="h-7 rounded-lg bg-gray-200/50 px-2.5 text-xs text-gray-600 transition-colors hover:text-gray-900 disabled:opacity-50 dark:bg-white/8 dark:text-gray-400 dark:hover:text-white"
										onclick={() => testConnection()}
										disabled={testing}>{testing ? '...' : $t('admin.browserTest')}</button
									>
								</div>
								{#if testResult}
									<p
										class="mt-1 text-[0.6875rem] {testResult.ok
											? 'text-emerald-600 dark:text-emerald-400'
											: 'text-gray-400 dark:text-gray-600'}"
									>
										{testResult.message}
									</p>
								{/if}
								<a
									href="chrome://inspect/#remote-debugging"
									target="_blank"
									rel="noopener noreferrer"
									class="mt-1 inline-block text-[0.6875rem] text-gray-400 hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
									>{$t('admin.personalChromeEnableDebugging')} ↗</a
								>
								<div class="mt-2 flex items-center justify-between">
									<div>
										<span class="text-xs text-gray-600 dark:text-gray-400"
											>{$t('admin.personalChromeConnection')}</span
										>
										<p class="text-[0.625rem] text-gray-400 dark:text-gray-600">
											{$t('admin.personalChromeConnectionHint')}
										</p>
									</div>
									<select
										bind:value={personalKeepAlive}
										class="cursor-pointer bg-transparent text-xs text-gray-600 outline-none dark:text-gray-400"
									>
										<option value={true}>{$t('admin.personalChromeKeepRunning')}</option>
										<option value={false}>{$t('admin.personalChromeStopUnused')}</option>
									</select>
								</div>
								<div class="mt-2 flex items-center justify-between">
									<span class="text-xs text-gray-400 dark:text-gray-600">
										{#if personalStatus?.status === 'playing'}
											{$t('admin.personalChromeConnected')}
											{#if personalStatus.session_count}
												· {personalStatus.session_count} {$t('admin.personalChromeTabs')}
											{/if}
										{:else if personalStatus?.status === 'lost'}
											{$t('admin.personalChromeLost')}
										{:else}
											{$t('admin.personalChromeDisconnected')}
										{/if}
									</span>
									<button
										class="text-xs text-gray-500 transition-colors hover:text-gray-900 disabled:opacity-50 dark:text-gray-500 dark:hover:text-white"
										onclick={togglePersonalChrome}
										disabled={connectingPersonal}
									>
										{connectingPersonal
											? '...'
											: personalStatus?.status === 'playing'
												? $t('admin.personalChromeDisconnect')
												: $t('admin.personalChromeConnect')}
									</button>
								</div>
							</div>
						{/if}
					{/if}
				</div>
			</div>

			<div class="mt-5">
				<p class="mb-2 text-xs text-gray-500 dark:text-gray-500">
					{$t('admin.agentBrowserTools')}
				</p>
				<div class="flex flex-col gap-2.5">
					<label class="flex items-center justify-between cursor-pointer">
						<span class="text-xs text-gray-600 dark:text-gray-400">{$t('admin.browserTools')}</span>
						<ToggleSwitch
							value={browserEnabled}
							onchange={(v) => {
								browserEnabled = v;
							}}
						/>
					</label>
					<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
						{$t('admin.browserHint')}
					</p>

					{#if browserEnabled}
						<div class="flex items-center justify-between">
							<span class="text-xs text-gray-600 dark:text-gray-400"
								>{$t('admin.browserProvider')}</span
							>
							<select
								bind:value={browserProvider}
								class="bg-transparent text-xs text-gray-600 dark:text-gray-400 outline-none cursor-pointer"
							>
								<option value="local">{$t('admin.browserLocalCdp')}</option>
								<option value="firecrawl">{$t('admin.browserFirecrawl')}</option>
								<option value="browser_use">{$t('admin.browserBrowserUse')}</option>
							</select>
						</div>
						<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
							{#if browserProvider === 'local'}
								{$t('admin.browserLocalHint')}
							{:else if browserProvider === 'firecrawl'}
								{$t('admin.browserFirecrawlHint')}
							{:else}
								{$t('admin.browserBrowserUseHint')}
							{/if}
						</p>

						{#if browserProvider === 'local'}
							<label class="flex items-center justify-between cursor-pointer">
								<div>
									<span class="text-xs text-gray-600 dark:text-gray-400"
										>{$t('admin.browserAutoLaunch')}</span
									>
									<p class="text-[0.625rem] text-gray-400 dark:text-gray-600">
										{$t('admin.browserAutoLaunchHint')}
									</p>
								</div>
								<ToggleSwitch
									value={autoLaunch}
									onchange={(v) => {
										autoLaunch = v;
									}}
								/>
							</label>

							<div>
								<label class="text-xs text-gray-600 dark:text-gray-400" for="cdp-url"
									>{$t('admin.browserCdpUrl')}</label
								>
								<div class="flex gap-1.5 mt-1">
									<input
										id="cdp-url"
										type="text"
										bind:value={cdpUrl}
										placeholder="http://localhost:9222"
										class="flex-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
									/>
									<button
										class="h-7 px-2.5 rounded-lg text-xs bg-gray-200/50 dark:bg-white/8 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors disabled:opacity-50"
										onclick={() => testConnection()}
										disabled={testing}>{testing ? '...' : $t('admin.browserTest')}</button
									>
								</div>
								{#if testResult}
									<p
										class="text-[0.6875rem] mt-1 {testResult.ok
											? 'text-emerald-600 dark:text-emerald-400'
											: 'text-gray-400 dark:text-gray-600'}"
									>
										{testResult.message}
									</p>
								{/if}
							</div>

							<div>
								<label class="text-xs text-gray-600 dark:text-gray-400" for="session-timeout"
									>{$t('admin.browserSessionTimeout')}</label
								>
								<div class="flex items-center gap-1.5 mt-1">
									<input
										id="session-timeout"
										type="number"
										bind:value={sessionTimeout}
										min="1"
										max="120"
										class="w-16 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
									/>
									<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
										>{$t('admin.browserMinutes')}</span
									>
								</div>
							</div>
						{:else if browserProvider === 'firecrawl'}
							<div>
								<label class="text-xs text-gray-600 dark:text-gray-400" for="fc-key"
									>{$t('admin.browserApiKey')}</label
								>
								<input
									id="fc-key"
									type="password"
									bind:value={firecrawlApiKey}
									placeholder="fc-..."
									class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
								/>
							</div>
							<div>
								<label class="text-xs text-gray-600 dark:text-gray-400" for="fc-url"
									>{$t('admin.browserBaseUrl')}</label
								>
								<input
									id="fc-url"
									type="text"
									bind:value={firecrawlBaseUrl}
									placeholder="https://api.firecrawl.dev"
									class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
								/>
								<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
									{$t('admin.browserFirecrawlBaseUrlHint')}
								</p>
							</div>
						{:else if browserProvider === 'browser_use'}
							<div>
								<label class="text-xs text-gray-600 dark:text-gray-400" for="bu-key"
									>{$t('admin.browserApiKey')}</label
								>
								<input
									id="bu-key"
									type="password"
									bind:value={browserUseApiKey}
									placeholder="bu-..."
									class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
								/>
							</div>
							<div>
								<label class="text-xs text-gray-600 dark:text-gray-400" for="bu-url"
									>{$t('admin.browserBaseUrl')}</label
								>
								<input
									id="bu-url"
									type="text"
									bind:value={browserUseBaseUrl}
									placeholder="https://api.browser-use.com"
									class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
								/>
							</div>
						{/if}
					{/if}
				</div>
			</div>
		</div>

		<!-- Save -->
		<div class="shrink-0 pt-3 flex justify-end">
			<button
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-50"
				onclick={() => save()}
				disabled={saving}
			>
				{#if saving}{$t('settings.saving')}{:else}{$t('settings.save')}{/if}
			</button>
		</div>
	{/if}
</div>
