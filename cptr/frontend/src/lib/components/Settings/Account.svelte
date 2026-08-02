<script lang="ts">
	import { toast } from 'svelte-sonner';
	import {
		updatePassword as apiUpdatePassword,
		uploadAvatar,
		deleteAvatar,
		updateProfile
	} from '$lib/apis/auth';
	import { ApiError } from '$lib/apis';
	import { session, setSession } from '$lib/session';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let currentPassword = $state('');
	let newPassword = $state('');
	let updatingPassword = $state(false);
	let uploadingAvatar = $state(false);
	let displayName = $state($session?.display_name ?? '');
	let savingProfile = $state(false);

	let avatarUrl = $derived($session?.profile_image_url);

	async function saveDisplayName() {
		const trimmed = displayName.trim();
		if (trimmed === ($session?.display_name ?? '')) return;
		savingProfile = true;
		try {
			await updateProfile(trimmed || null);
			if ($session) {
				setSession({ ...$session, display_name: trimmed || null });
			}
			toast.success($t('account.displayNameUpdated'));
		} catch (e) {
			toast.error(e instanceof ApiError ? e.message : $t('account.failedToUpdate'));
		} finally {
			savingProfile = false;
		}
	}

	async function updatePassword() {
		if (!currentPassword || !newPassword) {
			toast.error($t('account.bothFieldsRequired'));
			return;
		}
		if (newPassword.length < 6) {
			toast.error($t('auth.minChars'));
			return;
		}
		updatingPassword = true;
		try {
			await apiUpdatePassword(currentPassword, newPassword);
			toast.success($t('account.passwordUpdated'));
			currentPassword = '';
			newPassword = '';
		} catch (e) {
			toast.error(e instanceof ApiError ? e.message : $t('auth.connectionFailed'));
		} finally {
			updatingPassword = false;
		}
	}

	/** Resize image client-side to 256×256 before upload. */
	function resizeImage(file: File): Promise<Blob> {
		return new Promise((resolve, reject) => {
			const img = new Image();
			const url = URL.createObjectURL(file);
			img.onload = () => {
				URL.revokeObjectURL(url);
				const canvas = document.createElement('canvas');
				canvas.width = 256;
				canvas.height = 256;
				const ctx = canvas.getContext('2d')!;

				// Center crop to square
				const size = Math.min(img.width, img.height);
				const sx = (img.width - size) / 2;
				const sy = (img.height - size) / 2;
				ctx.drawImage(img, sx, sy, size, size, 0, 0, 256, 256);

				canvas.toBlob(
					(blob) => (blob ? resolve(blob) : reject(new Error('Canvas export failed'))),
					'image/png'
				);
			};
			img.onerror = () => {
				URL.revokeObjectURL(url);
				reject(new Error('Failed to load image'));
			};
			img.src = url;
		});
	}

	async function handleAvatarSelect() {
		const input = document.createElement('input');
		input.type = 'file';
		input.accept = 'image/*';
		input.onchange = async () => {
			const file = input.files?.[0];
			if (!file) return;
			uploadingAvatar = true;
			try {
				const resized = await resizeImage(file);
				const result = await uploadAvatar(resized);
				if ($session) {
					setSession({ ...$session, profile_image_url: result.profile_image_url });
				}
				toast.success($t('account.avatarUpdated'));
			} catch (e) {
				toast.error(e instanceof ApiError ? e.message : $t('account.uploadFailed'));
			} finally {
				uploadingAvatar = false;
			}
		};
		input.click();
	}

	async function handleAvatarDelete() {
		uploadingAvatar = true;
		try {
			await deleteAvatar();
			if ($session) {
				setSession({ ...$session, profile_image_url: null });
			}
			toast.success($t('account.avatarRemoved'));
		} catch (e) {
			toast.error(e instanceof ApiError ? e.message : $t('account.failedToRemoveAvatar'));
		} finally {
			uploadingAvatar = false;
		}
	}
</script>

<div class="flex flex-col min-h-full">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$t('account.title')}</h2>

	<!-- Avatar -->
	<div class="flex items-center gap-4 mb-6">
		<button
			class="relative w-10 h-10 rounded-full overflow-hidden bg-gray-100 dark:bg-white/8 flex items-center justify-center
			ring-1 ring-gray-200 dark:ring-white/10 hover:ring-2 hover:ring-gray-300 dark:hover:ring-white/20 transition-all duration-200 group shrink-0"
			onclick={handleAvatarSelect}
			disabled={uploadingAvatar}
		>
			{#if uploadingAvatar}
				<Spinner size={20} />
			{:else}
				<img src={avatarUrl || '/user.png'} alt="Avatar" class="w-full h-full object-cover" />
				<div
					class="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors duration-200 flex items-center justify-center"
				>
					<svg
						class="w-4 h-4 text-white opacity-0 group-hover:opacity-100 transition-opacity duration-200 drop-shadow-sm"
						fill="none"
						viewBox="0 0 24 24"
						stroke="currentColor"
						stroke-width="1.5"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192 0 0 0-1.736-1.039 48.774 48.774 0 0 0-5.232 0 2.192 2.192 0 0 0-1.736 1.039l-.821 1.316Z"
						/>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z"
						/>
					</svg>
				</div>
			{/if}
		</button>
		<div class="flex flex-col gap-1">
			<span class="text-sm font-medium text-gray-900 dark:text-white"
				>{$session?.display_name || $session?.username}</span
			>
			<div class="flex items-center gap-2">
				<button
					class="text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={handleAvatarSelect}
					disabled={uploadingAvatar}>{$t('account.uploadPhoto')}</button
				>
				{#if avatarUrl}
					<span class="text-gray-300 dark:text-gray-700 text-[0.6875rem]">·</span>
					<button
						class="text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
						onclick={handleAvatarDelete}
						disabled={uploadingAvatar}>{$t('account.removePhoto')}</button
					>
				{/if}
			</div>
		</div>
	</div>

	<!-- Display Name -->
	<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-1">{$t('account.displayName')}</h3>
	<input
		type="text"
		placeholder={$t('account.displayNamePlaceholder')}
		bind:value={displayName}
		class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-1 mb-5"
	/>

	<!-- Password -->
	<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-1">{$t('account.password')}</h3>
	<input
		type="password"
		placeholder={$t('account.currentPassword')}
		bind:value={currentPassword}
		autocomplete="current-password"
		class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-1"
	/>
	<input
		type="password"
		placeholder={$t('account.newPassword')}
		bind:value={newPassword}
		autocomplete="new-password"
		class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-1"
	/>
	<button
		disabled={updatingPassword || !currentPassword || !newPassword}
		onclick={updatePassword}
		class="mt-2 self-start text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100
		disabled:opacity-30 disabled:pointer-events-none"
	>
		{#if updatingPassword}
			<Spinner size={14} />
		{:else}
			{$t('account.updatePassword')}
		{/if}
	</button>

	<!-- Save -->
	<div class="mt-auto pt-6 flex justify-end">
		<button
			onclick={saveDisplayName}
			disabled={savingProfile}
			class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100
			disabled:opacity-30 disabled:pointer-events-none"
		>
			{#if savingProfile}
				{$t('settings.saving')}
			{:else}
				{$t('settings.save')}
			{/if}
		</button>
	</div>
</div>
