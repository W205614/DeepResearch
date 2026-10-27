"""Small complete two-image OCI/Docker bundle, never imported by production."""
import gzip
import hashlib
import io
import json
import tarfile


def bundle(*, missing_layer=False, wrong_diffid=False, aliased_layer=False):
    def encoded(value):
        return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    def digest(data):
        return 'sha256:'+hashlib.sha256(data).hexdigest()
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w'):
        pass
    layer=gzip.compress(raw.getvalue(),mtime=0)
    blobs={}
    def descriptor(data,kind):
        key=digest(data)
        blobs['blobs/sha256/'+key[7:]]=data
        return {'digest':key,'size':len(data),'mediaType':kind}
    compressed=descriptor(layer,'application/vnd.oci.image.layer.v1.tar+gzip')
    roots,legacy=[],[]
    for role in ['api','helper']:
        config=descriptor(encoded({'architecture':'amd64','os':'linux','config':{'Cmd':[role]},
            'rootfs':{'type':'layers','diff_ids':['sha256:'+'0'*64 if wrong_diffid else digest(raw.getvalue())]}}),
            'application/vnd.oci.image.config.v1+json')
        layer_descriptor = ({**compressed,'mediaType':'application/vnd.docker.image.rootfs.diff.tar.gzip'}
                            if aliased_layer and role=='helper' else compressed)
        manifest=descriptor(encoded({'schemaVersion':2,'config':config,'layers':[layer_descriptor]}),
                            'application/vnd.oci.image.manifest.v1+json')
        manifest['platform']={'os':'linux','architecture':'amd64'}
        index=descriptor(encoded({'schemaVersion':2,'manifests':[manifest]}),'application/vnd.oci.image.index.v1+json')
        roots.append(index)
        legacy.append({'Config':'blobs/sha256/'+config['digest'][7:],'Layers':['blobs/sha256/'+compressed['digest'][7:]]})
    if missing_layer:
        del blobs['blobs/sha256/'+compressed['digest'][7:]]
    blobs.update({'oci-layout':encoded({'imageLayoutVersion':'1.0.0'}),
                  'index.json':encoded({'schemaVersion':2,'manifests':roots}),'manifest.json':encoded(legacy)})
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w') as archive:
        for name,data in blobs.items():
            entry=tarfile.TarInfo(name)
            entry.size=len(data)
            archive.addfile(entry,io.BytesIO(data))
    return output.getvalue(),[root['digest'] for root in roots]
