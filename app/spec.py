COLUMNS = ('district', 'category', 'count', 'threshold', 'expected_hidden')
KINDS = ('cell', 'release')
PROJECT = {'name': '公开统计披露审核', 'actor': '统计公报制表员', 'stages': [('intake', '样例采信'), ('prepare', '切片完整性'), ('calculate', '单元格遮蔽'), ('policy', '反推检测'), ('balance', '运行对账')], 'chart': '遮蔽矩阵', 'mark': '公'}
