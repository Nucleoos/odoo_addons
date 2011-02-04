# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution    
#    Copyright (C) 2010 Smile (<http://www.smile.fr>). All Rights Reserved
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from osv import osv, fields

class ir_model_export_template(osv.osv):
    _name = 'ir.model.export.template'

    _columns = {
        'name': fields.char('Name', size=64, required=True),
        'model_id': fields.many2one('ir.model', 'Object', domain=[('osv_memory', '=', False)], required=True),
        'domain': fields.char('Domain', size=255),
        'unique': fields.boolean('Unique', help="If unique, each instance is exported only once"),
        'method': fields.char('Method', size=64, help="Indicate a method with a signature equals to (self, cr, uid, ids, fields_to_export, *args, context=None)"),
        'action_id': fields.many2one('ir.actions.server', 'Action'),
        'export_ids': fields.one2many('ir.model.export', 'export_tmpl_id', 'Exports'),
        'cron_id': fields.many2one('ir.cron', 'Scheduled Action'),
        'client_action_id': fields.many2one('ir.values', 'Client Action'),
        'client_action_server_id': fields.many2one('ir.actions.server', 'Client Action Server'),
    }

    _defaults = {
        'domain': lambda * a: '[]',
        'method': lambda * a: 'export_data',
    }

    def create_export(self, cr, uid, ids, context=None):
        if isinstance(ids, (int, long)):
            ids = [ids]
        export_pool = self.pool.get('ir.model.export')
        for id in ids:
            export_id = export_pool.create(cr, uid, {'export_tmpl_id': id}, context)
            export_pool.generate(cr, uid, export_id, context)
        return True

    def create_cron(self, cr, uid, ids, context=None):
        if isinstance(ids, (int, long)):
            ids = [ids]
        for template in self.browse(cr, uid, ids, context):
            if not template.cron_id:
                vals = {
                    'name': template.name,
                    'user_id': 1,
                    'model': template.model_id.model,
                    'function': 'create_export',
                    'args': '(%d,)' % template.id,
                    'numbercall':-1,
                }
                cron_id = self.pool.get('ir.cron').create(cr, uid, vals)
                template.write({'cron_id': cron_id})
        return True

    def create_client_action(self, cr, uid, ids, context=None):
        if isinstance(ids, (int, long)):
            ids = [ids]
        for template in self.browse(cr, uid, ids, context):
            if not template.client_action_id:
                vals = {
                    'name': template.name,
                    'model_id': template.model_id.id,
                    'state': 'code',
                    'code': """context['bypass_domain'] = True
self.pool.get('ir.model.export.template').create_export(cr, uid, %d, context)""" % (template.id,),
                }
                server_action_id = self.pool.get('ir.actions.server').create(cr, uid, vals, context)
                vals2 = {
                    'name': template.name,
                    'object': True,
                    'model_id': template.model_id.id,
                    'model': template.model_id.model,
                    'key2': 'client_action_multi',
                    'value_unpickle': 'ir.actions.server,%d' % server_action_id,
                }
                client_action_id = self.pool.get('ir.values').create(cr, uid, vals2, context)
                template.write({'client_action_id': client_action_id, 'client_action_server_id': server_action_id, })
        return True
ir_model_export_template()

class ir_model_export(osv.osv):
    _name = 'ir.model.export'
    _rec_name = 'export_tmpl_id'

    _columns = {
        'export_tmpl_id': fields.many2one('ir.model.export.template', 'Template', required=True, ondelete='cascade'),
        'model_id': fields.related('export_tmpl_id', 'model_id', type='many2one', relation='ir.model', string='Object', readonly=True),
        'domain': fields.related('export_tmpl_id', 'domain', type='char', size=255, string='Domain', readonly=True),
        'unique': fields.related('export_tmpl_id', 'unique', type='boolean', string='Unique', readonly=True),
        'method': fields.related('export_tmpl_id', 'method', type='char', size=64, string='Method', readonly=True),
        'action_id': fields.related('export_tmpl_id', 'action_id', type='many2one', relation='ir.actions.server', string='Action', readonly=True),
        'create_date': fields.datetime('Creation Date', readonly=True),
        'create_uid': fields.many2one('res.users', 'Creation User', readonly=True),
        'line_ids': fields.one2many('ir.model.export.line', 'export_id', 'Lines'),
    }

    _order = 'create_date desc'

    def create_export_lines(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        export_line_pool = self.pool.get('ir.model.export.line')
        # Remove export lines linked to current exports (ids)
        export_line_ids = export_line_pool.search(cr, uid, [('export_id', 'in', ids)])
        if export_line_ids:
            export_line_pool.unlink(cr, uid, export_line_ids)
        # Create new export lines linked for current exports (ids)
        for export in self.browse(cr, uid, ids):
            domain = []
            if not context.get('bypass_domain', False):
                domain += eval(export.domain)
            else:
                domain += [('id', 'in', context.get('active_ids', []))]
            if export.unique:
                export_line_ids = export_line_pool.search(cr, uid, [('export_id.export_tmpl_id.model_id', '=', export.model_id.id)])
                exported_object_ids = [line['res_id'] for line in export_line_pool.read(cr, uid, export_line_ids, ['res_id'])]
                domain += [('id', 'not in', exported_object_ids)]
            object_ids = self.pool.get(export.model_id.model).search(cr, uid, domain)
            if object_ids:
                for object_id in object_ids:
                    export_line_pool.create(cr, uid, {'export_id': export.id, 'res_id': object_id})
        return True

    def create(self, cr, uid, vals, context=None):
        export_id = super(ir_model_export, self).create(cr, uid, vals, context)
        self.create_export_lines(cr, uid, export_id, context)
        return export_id

    def write(self, cr, uid, ids, vals, context=None):
        res = super(ir_model_export, self).write(cr, uid, ids, vals, context)
        self.create_export_lines(cr, uid, ids, context)
        return res

    def generate(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        for export in self.browse(cr, uid, ids):
            try:
                if export.line_ids:
                    object_ids = [line.res_id for line in export.line_ids]
                    if object_ids:
                        if export.method:
                            field_pool = self.pool.get('ir.model.fields')
                            field_ids = field_pool.search(cr, uid, [('model_id', '=', export.model_id.id)])
                            fields_to_export = [field['name'] for field in field_pool.read(cr, uid, field_ids, ['name'])]
                            getattr(self.pool.get(export.model_id.model), export.method)(cr, uid, object_ids, fields_to_export, context=context)
                        if export.action_id:
                            for object_id in object_ids:
                                context_copy = dict(context)
                                context_copy['active_id'] = object_id
                                self.pool.get('ir.actions.server').run(cr, uid, export.action_id.id, context=context_copy)
            except Exception, e:
                raise e
        return True
ir_model_export()

class ir_model_export_line(osv.osv):
    _name = 'ir.model.export.line'
    _rec_name = 'export_id'

    _columns = {
        'export_id': fields.many2one('ir.model.export', 'Export', required=True, ondelete='cascade'),
        'res_id': fields.integer('Resource ID', required=True),
        'res_type': fields.related('export_id', 'mode_id', type='many2one', relation='ir.model', string='Resource Object'),
    }

    # TODO: manage object deletion => remove linked export lines
ir_model_export_line()