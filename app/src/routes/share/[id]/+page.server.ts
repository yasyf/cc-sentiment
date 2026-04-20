import { error } from '@sveltejs/kit';
import { addCacheTag } from '@vercel/functions';
import { fetchData, fetchMyStat, fetchShare } from '$lib/api.js';
import type { PageServerLoad } from './$types.js';

export const config = { isr: { expiration: 2_592_000 } };

export const load: PageServerLoad = async ({ fetch, params }) => {
	const record = await fetchShare(fetch, params.id);
	if (!record) throw error(404, 'Share not found');

	addCacheTag(`share:${record.id}`);

	const [stat, dashboard] = await Promise.all([
		fetchMyStat(fetch, record.contributor_id),
		fetchData(fetch),
	]);

	return { record, stat, ...dashboard };
};
