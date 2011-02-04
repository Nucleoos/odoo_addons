# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution    
#    Copyright (C) 2010 Smile. All Rights Reserved
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

from osv import fields, osv
import time
import datetime
import tools
from tools.translate import _
import netsvc
import re
import pytz
import traceback
import base64
import threading
import pooler

class actions_server_log(osv.osv):
    _name = 'ir.actions.server.log'
    _description = 'Server Action Log'
    _rec_name = 'action_id'

    _columns = {
        'action_id': fields.many2one('ir.actions.server', 'Action', select=True),
        'res_id': fields.integer('Resource'),
        'context': fields.text('Context'),
        'exception': fields.text('Exception'),
        'stack': fields.text('Stack Trace'),
        'create_date': fields.datetime('Creation Date', readonly=True),
    }

    _order = "create_date desc"
actions_server_log()

class actions_server(osv.osv):
    _inherit = 'ir.actions.server'
    _description = 'Server Action'

    _columns = {
        'active': fields.boolean('Active'),
        'log': fields.boolean('Log'),
    }

    _defaults = {
        'active': lambda * a: True,
    }

    def merge_message(self, cr, uid, keystr, action, context=None):
        if context is None:
            context = {}
        if not keystr:
            return str("")

        def is_a_datetime(str0, type='datetime'):
            if not isinstance(str0, str):
                return False
            formats = {
                'datetime': '%Y-%m-%d %H:%M:%S',
                'date': '%Y-%m-%d',
                'time': '%Y-%m-%d %H:%M:%S',
            }
            try:
                if type == 'time':
                    str0 = datetime.datetime.today().strftime(formats['date']) + ' ' + str0
                result = datetime.datetime.strptime(str0, formats[type])
                return result
            except Exception, e:
                return False

        def formatLang(value, lang=context.get('context_lang', 'en_US'), digits=2, tz=context.get('context_tz', 'Europe/London')):
            if not isinstance(value, (str, unicode)) or not value:
                return ''
            lang_pool = self.pool.get('res.lang')
            lang_id = lang_pool.search(cr, uid, [('code', '=', lang)], limit=1)
            if lang_id:
                lang = lang_pool.read(cr, uid, lang_id[0], ['date_format', 'time_format'])
                output_formats = {
                    'datetime': str(lang['date_format']) + ' ' + str(lang['time_format']),
                    'date': str(lang['date_format']),
                    'time': str(lang['time_format']),
                }
                for type in output_formats:
                    if is_a_datetime(value, type):
                        if tz:
                            return pytz.timezone(tz).fromutc(is_a_datetime(value)).strftime(output_formats[type])
                        else:
                            return is_a_datetime(value).strftime(output_formats[type])
                return lang_pool.format(self, cr, uid, lang_id, '%.' + str(digits) + 'f', value)
            return value

        def merge(match):
            logger = netsvc.Logger()
            obj_pool = self.pool.get(action.model_id.model)
            id = context.get('active_id')
            obj = obj_pool.browse(cr, uid, id, context)
            exp = str(match.group()[2:-2]).strip()
            localdict = {'object':obj, 'context': context, 'time':time, 'formatLang': formatLang}
            try:
                exec "result=" + exp.replace('&nbsp;', ' ') in localdict
                result = localdict['result']
                if is_a_datetime(result) or is_a_datetime(result, 'time'):
                    result = formatLang(result)
            except Exception, e:
                exception_text = "Exception: %s, rule: %s, %s." % (tools.ustr(e), match, keystr)
                logger.notifyChannel('ir.actions.server', netsvc.LOG_ERROR, exception_text)
                raise
            if result in (None, False):
                return str("")
            return tools.ustr(result)

        com = re.compile('(\[\[.+?\]\])')
        message = com.sub(merge, keystr)
        return message

    def run_now(self, cr, uid, ids, context=None):
        threaded_run = threading.Thread(target=self.run, args=(pooler.get_db(cr.dbname).cursor(), uid, ids, context))
        threaded_run.start()
        return True

    def run(self, cr, uid, ids, context=None):
        logger = netsvc.Logger()

        if context is None:
            context = {}
 
        if 'lang' not in context and 'context_tz' not in context:
            user = self.pool.get('res.users').read(cr, uid, uid, ['context_lang', 'context_tz'])
            if 'lang' not in context:
                context['lang'] = user['context_lang']
            if 'context_tz' not in context:
                context['context_tz'] = user['context_tz']

        if isinstance(ids, (int, long)):
            ids = [ids]

        for action in self.browse(cr, uid, ids, context):

            if action.active:

                if action.log:
                    log_id = self.pool.get('ir.actions.server.log').create(cr, uid, {
                        'action_id': action.id,
                        'res_id': context.get('active_id', False),
                        'context': context,
                    })

                try:

                    # Extracted to [OpenERPServerHome]/bin/addons/base/ir/ir_actions.py lines 581-593
                    obj_pool = self.pool.get(action.model_id.model)
                    obj = obj_pool.browse(cr, uid, context['active_id'], context=context)
                    cxt = {
                        'context':context,
                        'object': obj,
                        'time':time,
                        'cr': cr,
                        'pool' : self.pool,
                        'uid' : uid,
                    }
                    expr = eval(str(action.condition), cxt)
                    if not expr:
                        continue
                    # End of the extract

                    # Add a context more complete to object_create and object_write actions
                    if action.state == 'object_write':
                        res = {}
                        for exp in action.fields_lines:
                            euq = exp.value
                            if exp.type == 'equation':
                                expr = eval(euq, cxt)
                            else:
                                expr = exp.value
                            res[exp.col1.name] = expr

                        if not action.write_id:
                            if not action.srcmodel_id:
                                obj_pool = self.pool.get(action.model_id.model)
                                return obj_pool.write(cr, uid, [context.get('active_id')], res)
                            else:
                                write_id = context.get('active_id')
                                obj_pool = self.pool.get(action.srcmodel_id.model)
                                return obj_pool.write(cr, uid, [write_id], res)

                        elif action.write_id:
                            obj_pool = self.pool.get(action.srcmodel_id.model)
                            rec = self.pool.get(action.model_id.model).browse(cr, uid, context.get('active_id'))
                            id = eval(action.write_id, {'object': rec})
                            try:
                                id = int(id)
                            except:
                                raise osv.except_osv(_('Error'), _("Problem in configuration `Record Id` in Server Action!"))

                            if type(id) != type(1):
                                raise osv.except_osv(_('Error'), _("Problem in configuration `Record Id` in Server Action!"))
                            write_id = id
                            return obj_pool.write(cr, uid, [write_id], res)

                    elif action.state == 'object_create':
                        res = {}
                        for exp in action.fields_lines:
                            euq = exp.value
                            if exp.type == 'equation':
                                expr = eval(euq, cxt)
                            else:
                                expr = exp.value
                            res[exp.col1.name] = expr

                        obj_pool = None
                        res_id = False
                        obj_pool = self.pool.get(action.srcmodel_id.model)
                        res_id = obj_pool.create(cr, uid, res)
                        if action.record_id:
                            return self.pool.get(action.model_id.model).write(cr, uid, [context.get('active_id')], {action.record_id.name:res_id})
                        if res_id:
                            return True

                    elif action.state == 'object_copy':
                        res = {}
                        for exp in action.fields_lines:
                            euq = exp.value
                            if exp.type == 'equation':
                                expr = eval(euq, cxt)
                            else:
                                expr = exp.value
                            res[exp.col1.name] = expr

                        obj_pool = None
                        res_id = False

                        model = action.copy_object.split(',')[0]
                        cid = action.copy_object.split(',')[1]
                        obj_pool = self.pool.get(model)
                        return obj_pool.copy(cr, uid, int(cid), res)

                    else:
                        return super(actions_server, self).run(cr, uid, [action.id], context)

                except Exception, e:
                    stack = traceback.format_exc()
                    vals = {
                        'exception': tools.ustr(e),
                        'stack': tools.ustr(stack),
                    }
                    if action.log:
                        self.pool.get('ir.actions.server.log').write(cr, uid, log_id, vals, context)
                    else:
                        vals.update({
                            'action_id': action.id,
                            'res_id': context.get('active_id', False),
                            'context': context,
                        })
                        self.pool.get('ir.actions.server.log').create(cr, uid, vals, context)
        return True
actions_server()