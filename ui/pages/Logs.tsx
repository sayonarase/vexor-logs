import React, { useState } from 'react';
import { Stack, Title, Paper, Group, Button, Tabs, Text } from '@mantine/core';
import { IconSearch, IconBroadcast } from '@tabler/icons-react';
import { LogQueryInput } from '../components/LogQueryInput';
import { LogTable } from '../components/LogTable';
import { LogStreamLive } from '../components/LogStreamLive';
import { useLogQuery } from '../hooks/useLogs';

export default function Logs(): React.JSX.Element {
  const [query, setQuery] = useState('*');
  const [submitted, setSubmitted] = useState('*');
  const [tab, setTab] = useState<string | null>('search');
  const { data, isFetching, error } = useLogQuery(submitted, 500, tab === 'search');

  return (
    <Stack gap="md">
      <Title order={2}>Logs</Title>
      <Paper p="md" withBorder>
        <Stack gap="sm">
          <LogQueryInput value={query} onChange={setQuery} onSubmit={() => setSubmitted(query)} />
          <Group>
            <Button onClick={() => setSubmitted(query)} loading={isFetching} leftSection={<IconSearch size={16} />}>
              Search
            </Button>
            <Text c="dimmed" size="xs">Ctrl+Enter to run · LogsQL syntax</Text>
          </Group>
        </Stack>
      </Paper>
      <Tabs value={tab} onChange={setTab}>
        <Tabs.List>
          <Tabs.Tab value="search" leftSection={<IconSearch size={14} />}>Results</Tabs.Tab>
          <Tabs.Tab value="tail" leftSection={<IconBroadcast size={14} />}>Live-tail</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="search" pt="sm">
          {error ? <Text c="red">{(error as Error).message}</Text> : <LogTable rows={data?.rows ?? []} />}
        </Tabs.Panel>
        <Tabs.Panel value="tail" pt="sm">
          <LogStreamLive query={submitted} />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
