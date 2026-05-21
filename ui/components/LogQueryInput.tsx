import React from 'react';
import { Textarea } from '@mantine/core';

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit?: () => void;
}

// Lightweight LogsQL input. Mantine Textarea — no extra deps.
// Submit on Ctrl+Enter for parity with other query UIs.
export function LogQueryInput({ value, onChange, onSubmit }: Props): React.JSX.Element {
  return (
    <Textarea
      value={value}
      onChange={(e) => onChange(e.currentTarget.value)}
      onKeyDown={(e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && onSubmit) {
          e.preventDefault(); onSubmit();
        }
      }}
      placeholder='LogsQL — e.g.  _stream:{host="web01"} AND level:error'
      autosize
      minRows={2}
      maxRows={6}
      styles={{ input: { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' } }}
    />
  );
}
