# -*- coding: utf-8 -*-
#########################################################################
#
# Copyright (C) 2016 OSGeo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################

import json
import traceback

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.contrib.auth import get_user_model

from geonode.utils import resolve_object
from geonode.base.models import ResourceBase
from geonode.layers.models import Layer
from geonode.people.models import Profile

if "notification" in settings.INSTALLED_APPS:
    from notification import models as notification


def _perms_info(obj):
    info = obj.get_all_level_info()

    return info


def _perms_info_json(obj):
    info = _perms_info(obj)
    info['users'] = {u.username: perms for u, perms in info['users'].items()}
    info['groups'] = {g.name: perms for g, perms in info['groups'].items()}

    return json.dumps(info)


def resource_permissions(request, resource_id):
    try:
        resource = resolve_object(
            request, ResourceBase, {
                'id': resource_id}, 'base.change_resourcebase_permissions')

    except PermissionDenied:
        traceback.print_exc()
        # we are handling this in a non-standard way
        return HttpResponse(
            'You are not allowed to change permissions for this resource',
            status=401,
            content_type='text/plain')

    if request.method == 'POST':
        success = True
        message = "Permissions successfully updated!"
        try:
            permission_spec = json.loads(request.body.decode('UTF-8'))
            resource.set_permissions(permission_spec)

            # Check Users Permissions Consistency
            view_any = False
            info = _perms_info(resource)

            for user, perms in info['users'].items():
                if user.username == "AnonymousUser":
                    view_any = "view_resourcebase" in perms
                    break

            for user, perms in info['users'].items():
                if "download_resourcebase" in perms and \
                   "view_resourcebase" not in perms and \
                   not view_any:

                    success = False
                    message = "User {} has download permissions but cannot " \
                              "access the resource. Please update permission " \
                              "consistently!".format(user.username)

            return HttpResponse(
                json.dumps({'success': success, 'message': message}),
                status=200,
                content_type='text/plain'
            )
        except Exception:
            traceback.print_exc()
            success = False
            message = "Error updating permissions :("
            return HttpResponse(
                json.dumps({'success': success, 'message': message}),
                status=500,
                content_type='text/plain'
            )

    elif request.method == 'GET':
        permission_spec = _perms_info_json(resource)
        return HttpResponse(
            json.dumps({'success': True, 'permissions': permission_spec}),
            status=200,
            content_type='text/plain'
        )
    else:
        traceback.print_exc()
        return HttpResponse(
            'No methods other than get and post are allowed',
            status=401,
            content_type='text/plain')


@require_POST
def invalidate_permissions_cache(request):
    from .utils import sync_resources_with_guardian
    uuid = request.POST['uuid']
    resource = get_object_or_404(ResourceBase, uuid=uuid)
    can_change_permissions = request.user.has_perm(
        'change_resourcebase_permissions',
        resource)
    if can_change_permissions:
        # Push Security Rules
        sync_resources_with_guardian(resource)
        return HttpResponse(
            json.dumps({'success': 'ok', 'message': 'Security Rules Cache Refreshed!'}),
            status=200,
            content_type='text/plain'
        )
    else:
        traceback.print_exc()
        return HttpResponse(
            json.dumps({'success': 'false', 'message': 'You cannot modify this resource!'}),
            status=200,
            content_type='text/plain'
        )


@require_POST
def attributes_sats_refresh(request):
    from geonode.geoserver.helpers import gs_catalog, set_attributes_from_geoserver
    uuid = request.POST['uuid']
    resource = get_object_or_404(ResourceBase, uuid=uuid)
    can_change_data = request.user.has_perm(
        'change_resourcebase',
        resource)
    layer = Layer.objects.get(id=resource.id)
    if layer and can_change_data:
        try:
            # recalculate the layer statistics
            set_attributes_from_geoserver(layer, overwrite=True)
            gs_resource = gs_catalog.get_resource(
                name=layer.name,
                store=layer.store,
                workspace=layer.workspace)
            if not gs_resource:
                gs_resource = gs_catalog.get_resource(
                    name=layer.name,
                    workspace=layer.workspace)
            if not gs_resource:
                gs_resource = gs_catalog.get_resource(name=layer.name)

            if not gs_resource:
                return HttpResponse(
                    json.dumps(
                        {
                            'success': 'false',
                            'message': 'Error trying to fetch the resource "%s" from GeoServer!' % layer.store
                        }),
                    status=302,
                    content_type='text/plain')
            from decimal import Decimal
            layer.bbox_x0 = Decimal(gs_resource.native_bbox[0])
            layer.bbox_x1 = Decimal(gs_resource.native_bbox[1])
            layer.bbox_y0 = Decimal(gs_resource.native_bbox[2])
            layer.bbox_y1 = Decimal(gs_resource.native_bbox[3])
            layer.srid = gs_resource.projection
            layer.save()
        except Exception as e:
            traceback.print_exc()
            return HttpResponse(
                json.dumps(
                    {
                        'success': 'false',
                        'message': 'Exception occurred: "%s"' % str(e)
                    }),
                status=302,
                content_type='text/plain')
        return HttpResponse(
            json.dumps({'success': 'ok', 'message': 'Attributes/Stats Refreshed Successfully!'}),
            status=200,
            content_type='text/plain'
        )
    else:
        return HttpResponse(
            json.dumps({'success': 'false', 'message': 'You cannot modify this resource!'}),
            status=200,
            content_type='text/plain'
        )


@require_POST
def invalidate_tiledlayer_cache(request):
    from .utils import set_geowebcache_invalidate_cache
    uuid = request.POST['uuid']
    resource = get_object_or_404(ResourceBase, uuid=uuid)
    can_change_data = request.user.has_perm(
        'change_resourcebase',
        resource)
    layer = Layer.objects.get(id=resource.id)
    if layer and can_change_data:
        set_geowebcache_invalidate_cache(layer.alternate)
        return HttpResponse(
            json.dumps({'success': 'ok', 'message': 'GeoWebCache Tiled Layer Emptied!'}),
            status=200,
            content_type='text/plain'
        )
    else:
        return HttpResponse(
            json.dumps({'success': 'false', 'message': 'You cannot modify this resource!'}),
            status=200,
            content_type='text/plain'
        )


@require_POST
def set_bulk_permissions(request):
    permission_spec = json.loads(request.POST.get('permissions', None))
    resource_ids = request.POST.getlist('resources', [])
    if permission_spec is not None:
        not_permitted = []
        for resource_id in resource_ids:
            try:
                resource = resolve_object(
                    request, ResourceBase, {
                        'id': resource_id
                    },
                    'base.change_resourcebase_permissions')
                resource.set_permissions(permission_spec)
            except PermissionDenied:
                not_permitted.append(ResourceBase.objects.get(id=resource_id).title)

        return HttpResponse(
            json.dumps({'success': 'ok', 'not_changed': not_permitted}),
            status=200,
            content_type='text/plain'
        )
    else:
        return HttpResponse(
            json.dumps({'error': 'Wrong permissions specification'}),
            status=400,
            content_type='text/plain')


@require_POST
def request_permissions(request):
    """ Request permission to download a resource.
    """
    uuid = request.POST['uuid']
    resource = get_object_or_404(ResourceBase, uuid=uuid)
    try:
        notification.send(
            [resource.owner],
            'request_download_resourcebase',
            {'from_user': request.user, 'resource': resource}
        )
        return HttpResponse(
            json.dumps({'success': 'ok', }),
            status=200,
            content_type='text/plain')
    except Exception:
        traceback.print_exc()
        return HttpResponse(
            json.dumps({'error': 'error delivering notification'}),
            status=400,
            content_type='text/plain')


def send_email_consumer(layer_uuid, user_id):
    resource = get_object_or_404(ResourceBase, uuid=layer_uuid)
    user = Profile.objects.get(id=user_id)
    notification.send(
        [resource.owner],
        'request_download_resourcebase',
        {'from_user': user, 'resource': resource}
    )


def send_email_owner_on_view(owner, viewer, layer_id, geonode_email="email@geo.node"):
    # get owner and viewer emails
    owner_email = get_user_model().objects.get(username=owner).email
    layer = Layer.objects.get(id=layer_id)
    # check if those values are empty
    if owner_email and geonode_email:
        from django.core.mail import EmailMessage
        # TODO: Copy edit message.
        subject_email = "Your Layer has been seen."
        msg = ("Your layer called {0} with uuid={1}"
               " was seen by {2}").format(layer.name, layer.uuid, viewer)
        try:
            email = EmailMessage(
                subject=subject_email,
                body=msg,
                from_email=geonode_email,
                to=[owner_email, ],
                reply_to=[geonode_email, ])
            email.content_subtype = "html"
            email.send()
        except Exception:
            traceback.print_exc()
