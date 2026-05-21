import React, { useEffect, useRef, useState } from 'react';
import { Button, Group, ScrollArea, Code, Stack, Text } from '@mantine/core';

interface Props { query: string }

export function LogStreamLive({ query }: Props): React.JSX.Element {
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const esRef = useRef<EventSource | null>(null);

  const start = () => {
    if (!query) return;
    const es = new EventSource(`/api/v1/logs/tail?query=${encodeURIComponent(query)}`);
    es.onmessage = (ev) => {
      setLines((l) => [...l.slice(-499), ev.data]);
    };
    es.onerror = () => { es.close(); setRunning(false); };
    esRef.current = es; setRunning(true);
  };

  const stop = () => { esRef.current?.close(); esRef.current = null; setRunning(false); };

  useEffect(() => () => esRef.current?.close(), []);

  return (
    <Stack gap="sm">
      <Group>
        <Button size="xs" onClick={running ? stop : start} color={running ? 'red' : 'green'}>
          {running ? 'Stop tail' : 'Start live-tail'}
        </Button>
        <Text c="dimmed" size="xs">{lines.length} lines</Text>
      </Group>
      <ScrollArea h={300} style={{ border: '1px solid var(--mantine-color-default-border)', borderRadius: 4, padding: 8 }}>
        <Code block style={{ background: 'transparent', whiteSpace: 'pre-wrap' }}>
          {lines.join('\n')}
        </Code>
      </ScrollArea>
    </Stack>
  );
}
