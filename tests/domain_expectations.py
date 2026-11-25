EXPECTED = {'0001': (4, 4, 'READY'),
 '0002': (2, 0, 'READY'),
 '0003': (0, None, 'CELL_CONFLICT'),
 '0004': (2, 2, 'MARGINAL_INFERENCE')}
EVIDENCE = {'0001': {'suppressed': {'1:1': 'PRIMARY',
                         '1:2': 'SECONDARY',
                         '2:1': 'SECONDARY',
                         '2:2': 'SECONDARY'},
          'exposed': []},
 '0004': {'suppressed': {'1:1': 'PRIMARY', '1:2': 'SECONDARY'},
          'exposed': ['1:1', '1:2']}}
