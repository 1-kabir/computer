import { get, writable } from 'svelte/store';

export interface ConfirmOptions {
	title: string;
	message: string;
	confirmLabel?: string;
	cancelLabel?: string;
}

export interface ConfirmRequest extends Required<ConfirmOptions> {
	id: number;
	resolve: (confirmed: boolean) => void;
}

export const confirmDialog = writable<ConfirmRequest | null>(null);

let nextId = 0;

export function requestConfirm(options: ConfirmOptions): Promise<boolean> {
	if (typeof window === 'undefined') return Promise.resolve(false);

	const existing = get(confirmDialog);
	if (existing) existing.resolve(false);

	return new Promise((resolve) => {
		confirmDialog.set({
			id: ++nextId,
			title: options.title,
			message: options.message,
			confirmLabel: options.confirmLabel ?? 'Confirm',
			cancelLabel: options.cancelLabel ?? 'Cancel',
			resolve
		});
	});
}

export function settleConfirm(confirmed: boolean): void {
	const request = get(confirmDialog);
	if (!request) return;
	confirmDialog.set(null);
	request.resolve(confirmed);
}
