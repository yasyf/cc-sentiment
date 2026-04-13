import { fetchData } from '$lib/api.js';
import type { PageLoad } from './$types.js';

export const load: PageLoad = async ({ depends }) => {
	depends('api:data');
	const data = await fetchData();
	return { ...data };
};
