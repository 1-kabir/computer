import { fetchJSON, jsonBody } from './index';

export type BotData = {
	id: string;
	platform: string;
	name: string;
	workspace: string;
	model_id: string;
	token_masked: string;
	allowed_senders: string[];
	is_active: boolean;
	is_running: boolean;
	meta: Record<string, any> | null;
	created_at: number;
	updated_at: number;
};

export type BotForm = {
	platform: string;
	name: string;
	workspace: string;
	model_id: string;
	token: string;
	allowed_senders?: string[];
	is_active?: boolean;
	/** WhatsApp only: enables webhook signature verification. */
	app_secret?: string;
	/** WhatsApp only: string Meta echoes during the GET verification challenge. */
	webhook_verify_token?: string;
};

export type BotUpdate = {
	name?: string;
	workspace?: string;
	model_id?: string;
	token?: string;
	allowed_senders?: string[];
	is_active?: boolean;
	/** WhatsApp only: enables webhook signature verification. */
	app_secret?: string;
	/** WhatsApp only: string Meta echoes during the GET verification challenge. */
	webhook_verify_token?: string;
};

export async function listBots(): Promise<BotData[]> {
	return fetchJSON('/api/admin/bots');
}

export async function createBot(form: BotForm): Promise<BotData> {
	return fetchJSON('/api/admin/bots', jsonBody(form));
}

export async function updateBot(id: string, form: BotUpdate): Promise<BotData> {
	return fetchJSON(`/api/admin/bots/${id}`, {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(form)
	});
}

export async function deleteBot(id: string): Promise<{ ok: boolean }> {
	return fetchJSON(`/api/admin/bots/${id}`, { method: 'DELETE' });
}

export async function startBot(id: string): Promise<{ ok: boolean; is_running: boolean }> {
	return fetchJSON(`/api/admin/bots/${id}/start`, { method: 'POST' });
}

export async function stopBot(id: string): Promise<{ ok: boolean; is_running: boolean }> {
	return fetchJSON(`/api/admin/bots/${id}/stop`, { method: 'POST' });
}

export async function verifyBotToken(
	platform: string,
	token: string
): Promise<{ ok: boolean; info?: { username: string; id: string }; error?: string }> {
	return fetchJSON('/api/admin/bots/verify', jsonBody({ platform, token }));
}
