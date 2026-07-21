<script lang="ts">
	import { t } from '$lib/i18n';

	interface Props {
		item: any;
		pairedOutput: any;
		chatId: string | null;
		messageId: string;
		onanswer: (
			messageId: string,
			callId: string,
			answers: Record<string, string>,
			timedOut: boolean
		) => void;
	}

	let { item, pairedOutput, chatId, messageId, onanswer }: Props = $props();
	let selections = $state<Record<string, string>>({});
	let otherAnswers = $state<Record<string, string>>({});
	let submitting = $state(false);
	let questionIndex = $state(0);
	const request = $derived(item.arguments || {});
	const questions = $derived(Array.isArray(request.questions) ? request.questions : []);
	const question = $derived(questions[questionIndex]);
	const pending = $derived(item.status === 'pending');
	function hasAnswers(answers: Record<string, string>) {
		return (
			questions.length > 0 &&
			questions.every((question: any) => {
				const choice = answers[question.id];
				return choice && (choice !== '__other__' || otherAnswers[question.id]?.trim());
			})
		);
	}
	const canSubmit = $derived(hasAnswers(selections));
	const resolved = $derived.by(() => {
		try {
			return JSON.parse(pairedOutput?.output || '{}');
		} catch {
			return {};
		}
	});

	function submit(timedOut = false, selected = selections) {
		if (!chatId || (!hasAnswers(selected) && !timedOut)) return;
		const answers: Record<string, string> = {};
		for (const question of questions) {
			const choice = selected[question.id];
			answers[question.id] = timedOut
				? question.options[0].label
				: choice === '__other__'
					? otherAnswers[question.id].trim()
					: choice;
		}
		submitting = true;
		onanswer(messageId, item.call_id, answers, timedOut);
	}

	function selectAnswer(questionId: string, answer: string) {
		const selected = { ...selections, [questionId]: answer };
		selections = selected;
		if (answer === '__other__') return;
		advance(selected);
	}

	function advance(selected = selections) {
		if (questionIndex < questions.length - 1) {
			questionIndex += 1;
		} else if (hasAnswers(selected)) {
			submit(false, selected);
		}
	}
</script>

<section class="my-1 rounded-2xl bg-gray-100/70 px-3.5 py-3 dark:bg-white/[0.055]">
	<div class="mb-3 flex items-center justify-between gap-3">
		<div class="text-[0.6875rem] font-medium tracking-wide text-gray-500 dark:text-gray-400">
			{$t('chat.planningQuestion')}
		</div>
		{#if pending}
			<div class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$t('chat.questionProgress', { current: questionIndex + 1, total: questions.length })} ·
				{$t('chat.pausedWhileVisible')}
			</div>
		{:else if item.timed_out || resolved.timed_out}
			<div class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$t('chat.recommendationsUsed')}
			</div>
		{/if}
	</div>

	<div class="space-y-2.5">
		{#if question}
			{#key question.id}
				<div class="space-y-2.5">
					<div>
						<div class="text-sm font-medium tracking-[-0.01em] text-gray-900 dark:text-gray-100">
							{question.header}
						</div>
						<div class="mt-1 text-xs leading-relaxed text-gray-600 dark:text-gray-300">
							{question.question}
						</div>
					</div>
					{#if pending}
						<div class="space-y-0.5">
							{#each question.options || [] as option, optionIndex}
								<label
									class="flex cursor-pointer items-start gap-2.5 rounded-xl px-2.5 py-1.5 transition-colors {selections[
										question.id
									] === option.label
										? 'bg-white shadow-sm dark:bg-white/[0.1]'
										: 'hover:bg-white/70 dark:hover:bg-white/[0.06]'}"
									onclick={() => selections[question.id] === option.label && advance(selections)}
								>
									<input
										class="sr-only"
										type="radio"
										name={question.id}
										value={option.label}
										checked={selections[question.id] === option.label}
										onchange={() => selectAnswer(question.id, option.label)}
									/>
									<span
										class="mt-1 flex size-3.5 shrink-0 items-center justify-center rounded-full border {selections[
											question.id
										] === option.label
											? 'border-gray-900 dark:border-white'
											: 'border-gray-300 dark:border-white/25'}"
									>
										{#if selections[question.id] === option.label}
											<span class="size-1.5 rounded-full bg-gray-900 dark:bg-white"></span>
										{/if}
									</span>
									<span class="min-w-0 text-xs">
										<span class="text-gray-800 dark:text-gray-100">{option.label}</span>
										{#if optionIndex === 0}
											<span class="ml-1.5 text-[0.625rem] text-gray-400 dark:text-gray-500"
												>{$t('chat.recommended')}</span
											>
										{/if}
										<span class="mt-0.5 block leading-relaxed text-gray-500 dark:text-gray-400">
											{option.description}
										</span>
									</span>
								</label>
							{/each}
							<label
								class="flex cursor-pointer items-center gap-2.5 rounded-xl px-2.5 py-1.5 text-xs transition-colors {selections[
									question.id
								] === '__other__'
									? 'bg-white shadow-sm dark:bg-white/[0.1]'
									: 'hover:bg-white/70 dark:hover:bg-white/[0.06]'}"
							>
								<input
									class="sr-only"
									type="radio"
									name={question.id}
									value="__other__"
									checked={selections[question.id] === '__other__'}
									onchange={() => selectAnswer(question.id, '__other__')}
								/>
								<span
									class="flex size-3.5 shrink-0 items-center justify-center rounded-full border {selections[
										question.id
									] === '__other__'
										? 'border-gray-900 dark:border-white'
										: 'border-gray-300 dark:border-white/25'}"
								>
									{#if selections[question.id] === '__other__'}
										<span class="size-1.5 rounded-full bg-gray-900 dark:bg-white"></span>
									{/if}
								</span>
								<span class="text-gray-700 dark:text-gray-200">{$t('chat.other')}</span>
							</label>
							{#if selections[question.id] === '__other__'}
								<input
									class="w-full rounded-xl bg-transparent px-2.5 py-1.5 text-xs text-gray-800 outline-none placeholder:text-gray-400 dark:text-gray-100 dark:placeholder:text-gray-500"
									placeholder={$t('chat.typeYourAnswer')}
									value={otherAnswers[question.id] || ''}
									oninput={(event) =>
										(otherAnswers = { ...otherAnswers, [question.id]: event.currentTarget.value })}
								/>
							{/if}
						</div>
					{:else}
						<div class="text-xs leading-relaxed text-gray-600 dark:text-gray-300">
							{resolved.answers?.[question.id]?.answers?.join(', ') || $t('chat.noAnswerRecorded')}
						</div>
					{/if}
				</div>
			{/key}
		{/if}

		<div class="flex items-center justify-between gap-2 pt-0.5">
			<button
				type="button"
				class="rounded-lg px-2.5 py-1.5 text-xs text-gray-500 transition-colors hover:bg-white/70 hover:text-gray-800 disabled:opacity-30 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-gray-100"
				disabled={questionIndex === 0}
				onclick={() => (questionIndex -= 1)}
			>
				{$t('chat.previous')}
			</button>
			{#if questionIndex < questions.length - 1}
				<button
					type="button"
					class="rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition-transform active:scale-[0.98] dark:bg-white dark:text-black"
					onclick={() => (questionIndex += 1)}
				>
					{$t('chat.next')}
				</button>
			{:else if pending}
				<button
					type="button"
					class="rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition-transform active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-black"
					disabled={!canSubmit || submitting}
					onclick={() => submit()}
				>
					{$t('chat.submitAnswers')}
				</button>
			{/if}
		</div>
	</div>
</section>
