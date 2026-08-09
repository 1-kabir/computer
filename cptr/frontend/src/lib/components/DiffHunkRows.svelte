<script lang="ts">
	import { diffDisplayMode } from '$lib/stores/gitDiffSettings';
	import {
		groupDiffLines,
		numberDiffLines,
		splitDiffRows,
		withInlineDiffSegments,
		languageForPath,
		type DiffHunk,
		type DiffLine,
		type InlineDiffLine
	} from '$lib/utils/diff';
	import SyntaxDiffLine from './SyntaxDiffLine.svelte';

	interface Props {
		hunk: DiffHunk;
		path: string;
		showNumbers?: boolean;
	}

	let { hunk, path, showNumbers = true }: Props = $props();

	const lines = $derived(withInlineDiffSegments(numberDiffLines(hunk)));
	const language = $derived(languageForPath(path));

	function blockClass(type: DiffLine['type']): string {
		if (type === 'added') return 'bg-green-100 diff-gutter-added dark:bg-green-500/15';
		if (type === 'removed') return 'bg-red-100 diff-gutter-removed dark:bg-red-500/15';
		return '';
	}

	function textClass(type: DiffLine['type']): string {
		if (type === 'added') return 'text-green-900 dark:text-green-300';
		if (type === 'removed') return 'text-red-900 dark:text-red-300';
		return 'text-gray-600 dark:text-gray-400';
	}

	function prefixClass(type: DiffLine['type']): string {
		if (type === 'added') return 'text-green-600 dark:text-green-400';
		if (type === 'removed') return 'text-red-500 dark:text-red-400';
		return 'text-gray-400 dark:text-gray-600';
	}

	function linePrefix(type: DiffLine['type']): string {
		if (type === 'added') return '+';
		if (type === 'removed') return '-';
		return ' ';
	}

	function blankSegments(): InlineDiffLine['segments'] {
		return [{ text: ' ', changed: false }];
	}

	function splitCellClass(line: InlineDiffLine | null): string {
		if (!line) return '';
		if (line.type === 'added') return 'bg-green-100 dark:bg-green-500/15';
		if (line.type === 'removed') return 'bg-red-100 dark:bg-red-500/15';
		return '';
	}
</script>

{#if $diffDisplayMode === 'split'}
	{#each splitDiffRows(lines) as row}
		<div
			class="grid w-full {showNumbers
				? 'grid-cols-[2.75rem_minmax(0,1fr)_2.75rem_minmax(0,1fr)]'
				: 'grid-cols-[minmax(0,1fr)_minmax(0,1fr)]'}"
		>
			{#if showNumbers}
				<span
					class="select-none border-r border-black/5 px-2 text-right text-gray-400 dark:border-white/4 dark:text-gray-600 {splitCellClass(
						row.oldLine
					)}">{row.oldLine?.oldNumber ?? ''}</span
				>
			{/if}
			<div class="min-w-0 {splitCellClass(row.oldLine)}">
				{#if row.oldLine}
					<SyntaxDiffLine
						type={row.oldLine.type}
						content={row.oldLine.content || ' '}
						segments={row.oldLine.segments}
						{language}
						wrap
						class={textClass(row.oldLine.type)}
					/>
				{:else}
					<SyntaxDiffLine
						type="context"
						content=" "
						segments={blankSegments()}
						language="text"
						wrap
						class="text-transparent"
					/>
				{/if}
			</div>
			{#if showNumbers}
				<span
					class="select-none border-l border-r border-black/5 px-2 text-right text-gray-400 dark:border-white/4 dark:text-gray-600 {splitCellClass(
						row.newLine
					)}">{row.newLine?.newNumber ?? ''}</span
				>
			{/if}
			<div
				class="min-w-0 border-l border-black/5 dark:border-white/4 {splitCellClass(row.newLine)}"
			>
				{#if row.newLine}
					<SyntaxDiffLine
						type={row.newLine.type}
						content={row.newLine.content || ' '}
						segments={row.newLine.segments}
						{language}
						wrap
						class={textClass(row.newLine.type)}
					/>
				{:else}
					<SyntaxDiffLine
						type="context"
						content=" "
						segments={blankSegments()}
						language="text"
						wrap
						class="text-transparent"
					/>
				{/if}
			</div>
		</div>
	{/each}
{:else}
	{#each groupDiffLines(lines) as group}
		<div class="w-full {blockClass(group.type)}">
			{#each group.lines as line}
				{#if showNumbers}
					<div class="grid w-full grid-cols-[2.75rem_2.75rem_1.25rem_auto]">
						<span
							class="select-none border-r border-black/5 px-2 text-right text-gray-400 dark:border-white/4 dark:text-gray-600"
							>{line.oldNumber ?? ''}</span
						>
						<span
							class="select-none border-r border-black/5 px-2 text-right text-gray-400 dark:border-white/4 dark:text-gray-600"
							>{line.newNumber ?? ''}</span
						>
						<span class="select-none px-1 text-center {prefixClass(line.type)}"
							>{linePrefix(line.type)}</span
						>
						<SyntaxDiffLine
							type={line.type}
							content={line.content || ' '}
							segments={line.segments}
							{language}
							class={textClass(line.type)}
						/>
					</div>
				{:else}
					<div class="flex min-w-max">
						<span class="select-none px-2 text-center {prefixClass(line.type)}"
							>{linePrefix(line.type)}</span
						>
						<SyntaxDiffLine
							type={line.type}
							content={line.content || ' '}
							segments={line.segments}
							{language}
							class={textClass(line.type)}
						/>
					</div>
				{/if}
			{/each}
		</div>
	{/each}
{/if}

<style>
	.diff-gutter-added {
		box-shadow: inset 0.1875rem 0 0 #22c55e;
	}

	:global(.dark) .diff-gutter-added {
		box-shadow: inset 0.1875rem 0 0 #4ade80;
	}

	.diff-gutter-removed {
		background-image: repeating-linear-gradient(
			-45deg,
			#ef4444 0,
			#ef4444 1px,
			transparent 1px,
			transparent 0.1875rem
		);
		background-position: left top;
		background-repeat: repeat-y;
		background-size: 0.1875rem 0.1875rem;
	}
</style>
