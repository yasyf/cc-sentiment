<script lang="ts">
	import { onMount } from 'svelte';

	const STORAGE_KEY = 'cc-sentiment-cta-dismissed';
	const DELAY_MS = 20_000;
	const DISMISS_DAYS = 7;
	const COPY_CMD = 'uvx cc-sentiment';

	let visible = $state(false);
	let copied = $state(false);
	let reducedMotion = $state(false);

	let showTimeout: ReturnType<typeof setTimeout> | null = null;
	let copyTimeout: ReturnType<typeof setTimeout> | null = null;

	onMount(() => {
		reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

		const dismissed = localStorage.getItem(STORAGE_KEY);
		if (dismissed) {
			const ageDays = (Date.now() - new Date(dismissed).getTime()) / 86_400_000;
			if (Number.isFinite(ageDays) && ageDays >= 0 && ageDays < DISMISS_DAYS) return;
		}

		showTimeout = setTimeout(() => {
			visible = true;
		}, DELAY_MS);

		return () => {
			if (showTimeout) clearTimeout(showTimeout);
			if (copyTimeout) clearTimeout(copyTimeout);
		};
	});

	function dismiss() {
		localStorage.setItem(STORAGE_KEY, new Date().toISOString());
		visible = false;
	}

	async function copy() {
		await navigator.clipboard.writeText(COPY_CMD);
		copied = true;
		if (copyTimeout) clearTimeout(copyTimeout);
		copyTimeout = setTimeout(() => {
			copied = false;
		}, 1500);
	}
</script>

<aside
	aria-label="Contribute your data"
	aria-hidden={!visible}
	class="pointer-events-none fixed bottom-4 right-4 z-50 w-[min(20rem,calc(100vw-2rem))] transition-all duration-300 ease-out sm:bottom-6 sm:right-6"
	class:opacity-0={!visible}
	class:opacity-100={visible}
	class:translate-y-2={!visible && !reducedMotion}
	class:translate-y-0={visible && !reducedMotion}
>
	<div
		class="relative rounded-lg border border-border bg-bg-card p-4 shadow-lg"
		class:pointer-events-auto={visible}
	>
		<button
			type="button"
			onclick={dismiss}
			aria-label="Dismiss"
			class="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded text-text-dim transition-colors hover:bg-bg-hover hover:text-text"
		>
			<span aria-hidden="true" class="text-lg leading-none">×</span>
		</button>

		<h4 class="pr-6 text-sm font-semibold text-text">Add your sessions</h4>
		<p class="mt-1 text-xs text-text-muted">
			See yourself in the data. One command; scoring runs locally, only numeric scores are uploaded.
		</p>

		<div class="mt-3 flex items-center gap-2 rounded border border-border bg-bg-code px-3 py-2">
			<code class="flex-1 truncate font-mono text-xs text-text-secondary">$ {COPY_CMD}</code>
			<button
				type="button"
				onclick={copy}
				aria-label={copied ? 'Copied' : 'Copy command'}
				class="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium text-accent transition-colors hover:bg-bg-hover hover:text-accent-hover"
			>
				{copied ? 'Copied' : 'Copy'}
			</button>
		</div>

		<a
			href="/docs"
			class="mt-3 inline-block text-xs text-accent transition-colors hover:text-accent-hover"
		>
			How it works →
		</a>
	</div>
</aside>
