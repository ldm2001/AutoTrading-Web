<script lang="ts">
	let { text, variant = 'default' }: {
		text: string;
		variant?: 'default' | 'up' | 'down' | 'ai' | 'success' | 'warning';
	} = $props();
</script>

<span class="badge {variant}">{text}</span>

<style>
	.badge {
		display: inline-flex;
		align-items: center;
		font-size: 0.5625rem;
		font-weight: 700;
		padding: 0.0625rem 0.3125rem;
		border-radius: var(--radius-full);
		letter-spacing: 0.02em;
		flex-shrink: 0;
	}

	.default { background: var(--color-surface-3); color: var(--color-text-hint); }
	.up { background: rgba(239, 68, 68, 0.08); color: var(--color-up); }
	.down { background: rgba(59, 130, 246, 0.08); color: var(--color-down); }
	.ai { background: rgba(124, 58, 237, 0.1); color: var(--color-ai); }
	.success { background: rgba(34, 197, 94, 0.14); color: #166534; }
	.warning { background: rgba(245, 158, 11, 0.16); color: #92400e; }
</style>
