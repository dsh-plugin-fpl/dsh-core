from copy import deepcopy
from uuid import uuid4
import httpx
class FakeFeishu:

    def __init__(self):
        self.id = 'd' + uuid4().hex
        self.revision = 1
        self.blocks = [{'block_id': self.id, 'block_type': 1, 'children': ['old']}, {'block_id': 'old', 'block_type': 2, 'text': {'elements': [{'text_run': {'content': '远端原正文'}}]}}]
        self.writes = []
        self.token_requests = 0
        self.writable = True
        self.wiki_type = 'docx'
        self.wiki_tokens: list[str] = []
        self.hosts: set[str] = set()
        self.fail_token = False
        self.read_code: int | None = None
        self.timeout_after_append = False
        self.edit_after_append = False
        self.transport = httpx.MockTransport(self.handle)

    @property
    def url(self):
        return 'https://demo.feishu.cn/docx/' + self.id

    def edit(self, text='他人在飞书修改'):
        self.blocks[1]['text']['elements'][0]['text_run']['content'] = text
        self.revision += 1

    def handle(self, request):
        path = request.url.path
        self.hosts.add(request.url.host)
        if path.endswith('/get_node'):
            self.wiki_tokens.append(request.url.params.get('token', ''))
            if self.wiki_type == 'denied':
                return httpx.Response(400, json={'code': 131006, 'msg': 'provider secret'})
            if self.wiki_type == 'scope':
                return httpx.Response(400, json={'code': 99991672, 'msg': 'provider secret'})
            return httpx.Response(200, json={'code': 0, 'data': {'node': {'obj_type': self.wiki_type, 'obj_token': self.id, 'title': '飞书原标题'}}})
        if path.endswith('/tenant_access_token/internal'):
            self.token_requests += 1
            if self.fail_token:
                return httpx.Response(200, json={'code': 10003, 'msg': 'secret should never leak'})
            return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'test-token', 'expire': 3600})
        if request.method == 'GET' and self.read_code and ('/documents/' in path) and (not path.endswith('/blocks')):
            return httpx.Response(403, json={'code': self.read_code, 'msg': 'provider secret'})
        if '/permissions/' in path:
            data = {'auth_result': self.writable}
        elif request.method == 'GET' and path.endswith('/blocks'):
            data = {'items': deepcopy(self.blocks), 'has_more': False}
        elif request.method == 'GET':
            data = {'document': {'document_id': self.id, 'title': '飞书原标题', 'revision_id': self.revision}}
        elif path.endswith('/descendant'):
            import json
            body = json.loads(request.content)
            self.writes.append('append')
            ids = {b['block_id']: 'b' + uuid4().hex for b in body['descendants']}
            for block in body['descendants']:
                row = deepcopy(block)
                row['block_id'] = ids[block['block_id']]
                if 'children' in row:
                    row['children'] = [ids[id] for id in row['children']]
                self.blocks.append(row)
            self.blocks[0]['children'].extend((ids[id] for id in body['children_id']))
            self.revision += 1
            data = {'block_id_relations': [{'temporary_block_id': a, 'block_id': b} for a, b in ids.items()], 'document_revision_id': self.revision}
            if self.edit_after_append:
                self.edit()
            if self.timeout_after_append:
                raise httpx.ReadTimeout('ambiguous', request=request)
        elif path.endswith('/upload_all'):
            self.writes.append('upload')
            data = {'file_token': 'image-token'}
        elif request.method == 'PATCH':
            import json
            body = json.loads(request.content)
            id = path.rsplit('/', 1)[1]
            block = next((b for b in self.blocks if b['block_id'] == id))
            block['image'] = {'token': body['replace_image']['token']}
            self.writes.append('image')
            self.revision += 1
            data = {'document_revision_id': self.revision}
        elif request.method == 'DELETE':
            import json
            body = json.loads(request.content)
            self.writes.append('delete')
            roots = self.blocks[0]['children']
            removed = set(roots[body['start_index']:body['end_index']])
            while True:
                descendants = {c for b in self.blocks if b['block_id'] in removed for c in b.get('children', [])}
                if descendants <= removed:
                    break
                removed |= descendants
            self.blocks = [b for b in self.blocks if b['block_id'] not in removed]
            self.blocks[0]['children'] = roots[body['end_index']:]
            self.revision += 1
            data = {'document_revision_id': self.revision}
        else:
            raise AssertionError((request.method, path))
        return httpx.Response(200, json={'code': 0, 'data': data})
