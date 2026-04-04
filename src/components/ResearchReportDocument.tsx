import React from 'react';
import {
  Document,
  Page,
  Text,
  View,
  StyleSheet,
} from '@react-pdf/renderer';

// Known section headers the ReportComposer emits (plain-text, all-caps)
const SECTION_HEADERS = new Set([
  'EXECUTIVE SUMMARY',
  'KEY FINDINGS',
  'NOVEL INSIGHTS',
  'RECOMMENDATIONS',
  'KNOWLEDGE GAPS',
  'REFERENCES',
]);

const styles = StyleSheet.create({
  page: {
    paddingVertical: 52,
    paddingHorizontal: 60,
    fontFamily: 'Helvetica',
    backgroundColor: '#ffffff',
  },
  reportTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    marginBottom: 4,
    color: '#111827',
  },
  date: {
    fontSize: 9,
    color: '#6b7280',
    marginBottom: 20,
  },
  divider: {
    borderBottomWidth: 1,
    borderBottomColor: '#d1d5db',
    marginBottom: 20,
  },
  sectionHeader: {
    fontSize: 12,
    fontWeight: 'bold',
    color: '#1e40af',
    marginTop: 18,
    marginBottom: 6,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  body: {
    fontSize: 10.5,
    lineHeight: 1.65,
    color: '#374151',
    marginBottom: 3,
  },
  referencesHeader: {
    fontSize: 12,
    fontWeight: 'bold',
    color: '#6b7280',
    marginTop: 18,
    marginBottom: 6,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  referenceItem: {
    fontSize: 9.5,
    lineHeight: 1.5,
    color: '#4b5563',
    marginBottom: 2,
  },
});

/**
 * Parse the plain-text document produced by the ReportComposer into an array
 * of {type, text} segments so each section can be styled independently.
 */
function parseDocument(raw: string): { type: 'title' | 'header' | 'reference' | 'body'; text: string }[] {
  const segments: { type: 'title' | 'header' | 'reference' | 'body'; text: string }[] = [];
  const lines = raw.split('\n');

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    // "RESEARCH REPORT: ..." title line
    if (trimmed.startsWith('RESEARCH REPORT:')) {
      segments.push({ type: 'title', text: trimmed.replace(/^RESEARCH REPORT:\s*/i, '').trim() });
      continue;
    }

    // Known all-caps section headers
    if (SECTION_HEADERS.has(trimmed.toUpperCase())) {
      segments.push({ type: 'header', text: trimmed.toUpperCase() });
      continue;
    }

    // Reference lines: start with "[N]" where N is a number
    if (/^\[\d+\]/.test(trimmed)) {
      segments.push({ type: 'reference', text: trimmed });
      continue;
    }

    segments.push({ type: 'body', text: trimmed });
  }

  return segments;
}

interface ResearchReportDocumentProps {
  title: string;
  synthesis: string;
  generatedAt?: string;
}

export const ResearchReportDocument: React.FC<ResearchReportDocumentProps> = ({
  title,
  synthesis,
  generatedAt,
}) => {
  const segments = parseDocument(synthesis);
  const hasStructure = segments.some((s) => s.type === 'title' || s.type === 'header');

  return (
    <Document>
      <Page size="A4" style={styles.page}>
        <View>
          {/* Cover heading — use parsed title when available, else prop title */}
          <Text style={styles.reportTitle}>
            {hasStructure
              ? (segments.find((s) => s.type === 'title')?.text ?? title)
              : title}
          </Text>
          <Text style={styles.date}>
            Generated: {generatedAt ?? new Date().toLocaleDateString()}
          </Text>
          <View style={styles.divider} />

          {hasStructure ? (
            // Structured rendering: section headers + body paragraphs + references
            segments
              .filter((s) => s.type !== 'title') // title already rendered above
              .map((seg, i) => {
                if (seg.type === 'header') {
                  const isReferences = seg.text === 'REFERENCES';
                  return (
                    <Text key={i} style={isReferences ? styles.referencesHeader : styles.sectionHeader}>
                      {seg.text}
                    </Text>
                  );
                }
                if (seg.type === 'reference') {
                  return <Text key={i} style={styles.referenceItem}>{seg.text}</Text>;
                }
                return <Text key={i} style={styles.body}>{seg.text}</Text>;
              })
          ) : (
            // Fallback: render the whole synthesis as a plain text block
            <Text style={styles.body}>{synthesis}</Text>
          )}
        </View>
      </Page>
    </Document>
  );
};