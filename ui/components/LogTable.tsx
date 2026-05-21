import React from 'react';
import { Table, ScrollArea, Text } from '@mantine/core';
import type { LogRow } from '../hooks/useLogs';

interface Props { rows: LogRow[] }

export function LogTable({ rows }: Props): React.JSX.Element {
  if (!rows.length) return <Text c="dimmed" size="sm">no results</Text>;
  return (
    <ScrollArea h={500}>
      <Table striped withRowBorders={false} highlightOnHover stickyHeader>
        <Table.Thead>
          <Table.Tr>
            <Table.Th style={{ width: 200 }}>time</Table.Th>
            <Table.Th style={{ width: 140 }}>host</Table.Th>
            <Table.Th>message</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r, i) => (
            <Table.Tr key={i}>
              <Table.Td style={{ fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
                {String(r._time ?? '')}
              </Table.Td>
              <Table.Td>{String((r as Record<string, unknown>).host ?? '')}</Table.Td>
              <Table.Td style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                {String(r._msg ?? JSON.stringify(r))}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}
