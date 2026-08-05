/**
 * TipTap Mention extension configured for / command suggestions.
 */

import Mention from '@tiptap/extension-mention';

export function createSlashCommandMention(suggestionOptions: {
	items: (props: { query: string }) => unknown[] | Promise<unknown[]>;
	render: () => {
		onStart: (props: any) => void;
		onUpdate: (props: any) => void;
		onKeyDown: (props: any) => boolean;
		onExit: () => void;
	};
}) {
	return Mention.extend({ name: 'slashCommandMention' }).configure({
		suggestion: {
			char: '/',
			allowSpaces: false,
			...suggestionOptions
		}
	});
}
