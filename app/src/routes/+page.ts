import { fetchData } from '$lib/api.js';
import type { PageLoad } from './$types.js';

export const load: PageLoad = async ({ fetch }) => {
	const data = await fetchData(fetch);
	return { data };
};
