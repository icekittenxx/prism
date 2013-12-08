#coding: utf-8
import monkey
import pdb
import re
from itertools import ifilter
from email import message_from_file, message_from_string
from email.header import decode_header

from pymongo import MongoClient
from bson.objectid import ObjectId
from bson import Binary
from django.conf import settings
from django.core.urlresolvers import reverse
from django.utils.html import strip_tags
import HTMLParser

from exceptions import ObjectDoesNotExist
import attachreader

client = MongoClient(settings.DB_HOST, settings.DB_PORT)
db = client.prism
email_db = db.email

# Match encoded-word strings in the form =?charset?q?Hello_World?=
# Some will surrend it by " or end by , or by fucking \r
ecre = re.compile(r"""=\?([^?]*?)\?([qb])\?(.*?)\?=(?=\W|$)""",
        re.VERBOSE | re.IGNORECASE | re.MULTILINE)

strip_html_entities = HTMLParser.HTMLParser().unescape

def decode_str(str_enc):
    """Decode strings like =?charset?q?Hello_World?="""
    def decode_match(field):
        str_dec, charset = decode_header(field.group(0))[0]
        if charset:
            str_dec = str_dec.decode(charset, 'replace')
        return str_dec
    return ecre.sub(decode_match, str_enc)

def normalize_header(hdr):
    """Make keys lower case, filter out unneeded, etc.
    hdr must be a email.message type"""
    vanilla_hdr = {}

    for k, v in hdr.items():
        k = k.lower()
        # Filter out those added by other gateways
        if not k.startswith('x'):
            vanilla_hdr[k] = decode_str(v)
    
    # Ensure there is a content-type key
    # if 'content-type' not in vanilla_hdr:
    # get_content_type() will return a default one, all in lower-case
    vanilla_hdr['content-type'] = hdr.get_content_type()
    
    if hdr.get_filename():
        # for that default message handler
        vanilla_hdr['filename'] = hdr.get_filename()

    vanilla_hdr.pop('content-disposition', None)
    #TODO not everyone need it
    # if 'content-disposition' not in vanilla_hdr and hdr.get_filename():
    #     vanilla_hdr['content-disposition'] = hdr.get_filename()
    # if 'content-disposition' in vanilla_hdr:
    #     print '+'*20, vanilla_hdr['content-disposition'] 
    return vanilla_hdr

class Message(object):
    """This is a base object which provides basic method needed to parse any
    any email message, you can override those methods to suite a particular message"""

    # Some resources may need idx to identify themself
    def __init__(self, header, body, id=None, idx=0, body_html='', body_txt='', attachment=[], attach_txt=''):
        self.header = header
        self.body = body
        # Fetch one if not exist
        self.id = ObjectId(id)
        if type(idx) is not int:
            raise RuntimeError('%s in %s receives an invalid idx: %s' % 
                    (self.__class__.__name__, self.id, idx))
        self.idx = idx
        # if not body_html:
        #     body_html = self.to_html()
        self.body_html = body_html
        # if not body_txt:
        #     body_txt = strip_tags(self.body_html)
        self.body_txt = body_txt
        self.attachment = attachment
        self.attach_txt = attach_txt

    def __unicode__(self):
        return unicode(self.header.get('subject', self.id))

    def to_html(self):
        raise NotImplementedError('%s doesnot need to_html' %
                self.__class__.__name__)

    def to_txt(self):
        #TODO Unsafe, <script<script>>alert("Hi!")<</script>/script>
        return strip_html_entities(strip_tags(self.body_html)).strip()

    def to_dict(self):
        return {'header': self.header, 'body': self.body}
    
    @classmethod
    def from_msg(cls, msg, id=None, idx=0):
        id = ObjectId(id)
        header = normalize_header(msg)
        body = Binary(msg.get_payload(decode=True))
        return cls(header, body, id, idx=idx)

    @classmethod
    def from_dict(cls, d, idx=0):
        header = d.get('header')
        body = d.get('body', '')
        id = d.get('_id')
        body_html = d.get('body_html', '')
        body_txt = d.get('body_txt', '')
        attachment = d.get('attachment', [])
        attach_txt = d.get('attach_txt', '')
        return cls(header, body, id, idx, body_html=body_html, body_txt=body_txt, attachment=attachment, attach_txt=attach_txt)

    def get_resource(self, idx=0):
        if idx != 0:
            raise ObjectDoesNotExist()
        return self

    def save(self, **extra):
        """Any message who runs this method must be the root,
        we will to put meta info in the root"""
        d = self.to_dict()
        # modify dict here, because we need to put extra info in the outer msg
        d['_id'] = self.id
        if not self.body_html:
            self.body_html = self.to_html()
        if not self.body_txt:
            self.body_txt = self.to_txt()
        d['body_html'] = self.body_html
        d['body_txt'] = self.body_txt
        d['attachment'] = self.attachment
        d['attach_txt'] = self.attach_txt
        d.update(**extra)
        # Returns an ObjectId, we don't care about success write
        # Passing w=0 disables write acknowledgement to improve performance
        email_db.insert(d, w=0) 
        return self.id

class TextMessage(Message):

    @classmethod
    def from_msg(cls, msg, id=None, idx=0):
        id = ObjectId(id)
        # assert msg.get_content_maintype() == 'text'
        charset = msg.get_content_charset('gbk')  # gbk will be the default one
        body = msg.get_payload(decode=True).decode(charset, 'replace')
        header = normalize_header(msg)
        return cls(header, body, id, idx=idx)

    def to_html(self):
        return self.body

    def get_resource(self, idx=0):
        rsc = super(TextMessage, self).get_resource()
        rsc.header['content-type'] = '%s; charset=utf-8' % \
                rsc.header['content-type']
        return rsc

class ImageMessage(Message):
    html_tmpl = '<img border="0" hspace="0" align="baseline" src="%s" />'

    def to_html(self):
        if getattr(self, 'id', None) is None:
            raise RuntimeError("You havn't set my id yet")
        return self.html_tmpl % reverse('resource', args=(self.id, self.idx))

class ApplicationMessage(Message):

    def to_html(self):
        return ''

    @classmethod
    def from_msg(cls, msg, id=None, idx=0):
        appmsg = super(ApplicationMessage, cls).from_msg(msg, id, idx)
        filename = decode_str(msg.get_filename(u'未命名文件'))
        appmsg.attachment = [{'filename': filename,
            'url': reverse('resource', args=(appmsg.id, appmsg.idx))}]
        appmsg.attach_txt = attachreader.read(msg.get_payload(decode=True),
            filename)
        return appmsg

class DefaultMessage(ImageMessage):
    html_tmpl = "<a href='%s'>%s</a>"

    def to_html(self):
        if getattr(self, 'id', None) is None:
            raise RuntimeError("You havn't set my id yet")
        return self.html_tmpl % (reverse('resource', args=(self.id, self.idx)),
            self.header.get('filename', u'未命名文件'))

class MultipartMessage(Message):
    alternatives = ['text/html', 'text/richtext', 'text/plain', 'message/rfc822']

    @classmethod
    def from_msg(cls, msg, id=None, idx=0):  # 0 for add operation
        id = ObjectId(id)  # fetch one if not exist
        multimsg = []
        attachment = []
        for i, sub_msg in enumerate(msg.get_payload()):
            my_sub_msg = from_msg(sub_msg, id, i+idx)
            multimsg.append(my_sub_msg)  # no hieracy, just flatten them
            attachment.extend(my_sub_msg.attachment)
        attach_txt = ' '.join(a.attach_txt for a in multimsg)

        # Each of the parts is an "alternative" version of the same information.
        if msg.get_content_subtype() == 'alternative':
            for ct in cls.alternatives:
                # There must be a content-type, just in case
                best = next(ifilter(lambda m: m.header.get \
                    ('content-type', '').startswith(ct), multimsg), None)
                if best:
                    break
            else:
                # the last one means the richest, but maybe I donnot know
                # how to interpret, so just get the first one
                best = multimsg[0]
            # We still need your header, but donot overwrite headers I already have
            for k, v in normalize_header(msg).items():
                if k not in best.header:
                    best.header[k] = v
            return best
        else:
            header = normalize_header(msg)
            return cls(header, multimsg, id=id, idx=idx, attachment=attachment, attach_txt=attach_txt)
    
    @classmethod
    def from_dict(cls, d, idx=0):
        id = d['_id']
        header = d.get('header')
        children = []
        for i, child in enumerate(d.get('body', [])):
            child['_id'] = id
            children.append(from_dict(child, i+idx))
        body_html = d.get('body_html', '')
        body_txt = d.get('body_txt', '')
        attachment = d.get('attachment', [])
        attach_txt = d.get('attach_txt', '')
        return cls(header, children, id, idx=idx, body_html=body_html,
                body_txt=body_txt, attachment=attachment, attach_txt=attach_txt)

    def to_dict(self):
        d = super(MultipartMessage, self).to_dict()
        d['body'] = [b.to_dict() for b in self.body]
        return d
    
    def to_html(self, idx=None):
        child_html = []
        for child in self.body:
            child_html.append(child.to_html())
        return ''.join(child_html)

    def get_resource(self, idx):
        if idx is None:
            raise ObjectDoesNotExist()
        idx = int(idx)
        try:
            return self.body[idx]
        except IndexError:
            raise ObjectDoesNotExist()

parser = {'text': TextMessage,
        'image': ImageMessage,
        'application': ApplicationMessage,
        'multipart': MultipartMessage,}

def from_fp(fp):
    msg = message_from_file(fp)
    return from_msg(msg)

def from_string(fp):
    msg = message_from_string(fp)
    return from_msg(msg)

def from_msg(msg, id=None, idx=0):
    id = ObjectId(id)  # fetch one if not exist
    # May raise MessageParseError, I catch it in the view
    maintype = msg.get_content_maintype()
    return parser.get(maintype, DefaultMessage).from_msg(msg, id, idx)

def from_id(id_str):
    if not ObjectId.is_valid(id_str):
        raise ObjectDoesNotExist()
    msg_dict = email_db.find_one({'_id': ObjectId(id_str)})
    if not msg_dict:
        raise ObjectDoesNotExist()
    return from_dict(msg_dict)

def from_dict(d, idx=0):
    ct = d['header']['content-type']
    maintype = ct.split('/')[0]
    return parser.get(maintype, DefaultMessage).from_dict(d, idx)

def all():
    return map(from_dict, email_db.find())

def find(**selector):
    # return map(from_dict, email_db.find(selector))
    # Just return raw data, no need to wrap them
    return email_db.find(selector)

def remove(id_str):
    if ObjectId.is_valid(id_str):
        return email_db.remove(ObjectId(id_str))
    return None

