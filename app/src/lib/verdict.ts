export type VerdictSeverity = 1 | 2 | 3 | 4 | 5;

export interface Verdict {
	text: string;
	severity: VerdictSeverity;
}

export function verdictFor(overallAvg: number): Verdict {
	if (overallAvg < 2.0) return { text: 'Developers are frustrated.', severity: 1 };
	if (overallAvg < 2.5) return { text: 'Developers are struggling.', severity: 2 };
	if (overallAvg < 3.5) return { text: 'Developers are getting by.', severity: 3 };
	if (overallAvg < 4.0) return { text: 'Developers are happy.', severity: 4 };
	return { text: 'Developers are thriving.', severity: 5 };
}
