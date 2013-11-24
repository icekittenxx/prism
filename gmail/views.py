from django.http import HttpResponse
from django.views.generic import ListView, DetailView

from models import Email

class EmailList(ListView):
    template_name = 'email_list.html'
    context_object_name = 'eids'

    def get_queryset(self):
        return map(lambda e: str(e['_id']), Email.all())

    def post(self, req):
        email = Email(req)
        email.save()
        return HttpResponse('{ok: true, _id: %s}' % email._id)

    def delete(self, req):
        Email.remove(self.kwargs['eid'])
        return HttpResponse('dood')

class EmailDetail(DetailView):
    template_name = 'email_detail.html'
    context_object_name = 'email'

    def get_object(self):
        return Email.get(self.kwargs['eid'])

