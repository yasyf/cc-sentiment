<script lang="ts">
	import { onDestroy } from 'svelte';

	const COPY_CMD = 'uvx cc-sentiment';

	let copied = $state(false);
	let copyTimeout: ReturnType<typeof setTimeout> | null = null;

	async function copy() {
		await navigator.clipboard.writeText(COPY_CMD);
		copied = true;
		if (copyTimeout) clearTimeout(copyTimeout);
		copyTimeout = setTimeout(() => {
			copied = false;
		}, 1500);
	}

	onDestroy(() => {
		if (copyTimeout) clearTimeout(copyTimeout);
	});
</script>

<button
	type="button"
	onclick={copy}
	aria-label={copied ? 'Copied' : `Copy command: ${COPY_CMD}`}
	class="group -mx-2 flex w-full items-center gap-2 rounded px-2 py-1 text-left transition-colors hover:bg-bg-hover focus-visible:outline focus-visible:outline-1 focus-visible:outline-border"
>
	<code class="flex-1 truncate font-mono text-sm text-text-secondary">$ {COPY_CMD}</code>
	<span
		class="shrink-0 text-[11px] transition-colors {copied
			? 'text-text-muted'
			: 'text-text-dim group-hover:text-text-muted'}"
	>
		{copied ? 'Copied' : 'Copy'}
	</span>
</button>
