<script lang="ts">
	import Sun from '@lucide/svelte/icons/sun';
	import Moon from '@lucide/svelte/icons/moon';
	import Check from '@lucide/svelte/icons/check';
	import { DropdownMenu } from 'bits-ui';
	import { userPrefersMode, mode, setMode, resetMode } from 'mode-watcher';

	const MODES = [
		['light', 'Light'],
		['dark', 'Dark'],
		['system', 'System']
	] as const;

	const current = $derived(userPrefersMode.current ?? 'system');
	const resolved = $derived(mode.current ?? 'light');
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger
		aria-label="Toggle theme"
		class="inline-flex h-7 w-7 items-center justify-center rounded-md text-text-muted hover:text-text hover:bg-bg-hover transition-colors"
	>
		{#if resolved === 'dark'}
			<Moon class="h-4 w-4" />
		{:else}
			<Sun class="h-4 w-4" />
		{/if}
	</DropdownMenu.Trigger>
	<DropdownMenu.Portal>
		<DropdownMenu.Content
			align="end"
			sideOffset={6}
			class="min-w-[8rem] rounded-md border border-border bg-bg-card p-1 text-sm text-text shadow-md outline-none"
		>
			{#each MODES as [value, label] (value)}
				<DropdownMenu.Item
					onSelect={() => (value === 'system' ? resetMode() : setMode(value))}
					class="flex cursor-pointer items-center justify-between rounded px-2 py-1.5 text-text-muted outline-none data-[highlighted]:bg-bg-hover data-[highlighted]:text-text"
				>
					<span>{label}</span>
					{#if current === value}
						<Check class="h-3.5 w-3.5 text-accent" />
					{/if}
				</DropdownMenu.Item>
			{/each}
		</DropdownMenu.Content>
	</DropdownMenu.Portal>
</DropdownMenu.Root>
