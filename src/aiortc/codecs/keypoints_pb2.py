# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: keypoints.proto

import sys
_b=sys.version_info[0]<3 and (lambda x:x) or (lambda x:x.encode('latin1'))
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor.FileDescriptor(
  name='keypoints.proto',
  package='keypoint',
  syntax='proto2',
  serialized_options=None,
  serialized_pb=_b('\n\x0fkeypoints.proto\x12\x08keypoint\"&\n\x08Keypoint\x12\x0c\n\x04xloc\x18\x01 \x02(\x02\x12\x0c\n\x04yloc\x18\x02 \x02(\x02\">\n\x08Jacobian\x12\x0b\n\x03\x64\x31\x31\x18\x01 \x02(\x02\x12\x0b\n\x03\x64\x31\x32\x18\x02 \x02(\x02\x12\x0b\n\x03\x64\x32\x31\x18\x03 \x02(\x02\x12\x0b\n\x03\x64\x32\x32\x18\x04 \x02(\x02\"\\\n\x0cKeypointInfo\x12%\n\tkeypoints\x18\x01 \x03(\x0b\x32\x12.keypoint.Keypoint\x12%\n\tjacobians\x18\x02 \x03(\x0b\x32\x12.keypoint.Jacobian')
)




_KEYPOINT = _descriptor.Descriptor(
  name='Keypoint',
  full_name='keypoint.Keypoint',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='xloc', full_name='keypoint.Keypoint.xloc', index=0,
      number=1, type=2, cpp_type=6, label=2,
      has_default_value=False, default_value=float(0),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='yloc', full_name='keypoint.Keypoint.yloc', index=1,
      number=2, type=2, cpp_type=6, label=2,
      has_default_value=False, default_value=float(0),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto2',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=29,
  serialized_end=67,
)


_JACOBIAN = _descriptor.Descriptor(
  name='Jacobian',
  full_name='keypoint.Jacobian',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='d11', full_name='keypoint.Jacobian.d11', index=0,
      number=1, type=2, cpp_type=6, label=2,
      has_default_value=False, default_value=float(0),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='d12', full_name='keypoint.Jacobian.d12', index=1,
      number=2, type=2, cpp_type=6, label=2,
      has_default_value=False, default_value=float(0),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='d21', full_name='keypoint.Jacobian.d21', index=2,
      number=3, type=2, cpp_type=6, label=2,
      has_default_value=False, default_value=float(0),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='d22', full_name='keypoint.Jacobian.d22', index=3,
      number=4, type=2, cpp_type=6, label=2,
      has_default_value=False, default_value=float(0),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto2',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=69,
  serialized_end=131,
)


_KEYPOINTINFO = _descriptor.Descriptor(
  name='KeypointInfo',
  full_name='keypoint.KeypointInfo',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='keypoints', full_name='keypoint.KeypointInfo.keypoints', index=0,
      number=1, type=11, cpp_type=10, label=3,
      has_default_value=False, default_value=[],
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='jacobians', full_name='keypoint.KeypointInfo.jacobians', index=1,
      number=2, type=11, cpp_type=10, label=3,
      has_default_value=False, default_value=[],
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto2',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=133,
  serialized_end=225,
)

_KEYPOINTINFO.fields_by_name['keypoints'].message_type = _KEYPOINT
_KEYPOINTINFO.fields_by_name['jacobians'].message_type = _JACOBIAN
DESCRIPTOR.message_types_by_name['Keypoint'] = _KEYPOINT
DESCRIPTOR.message_types_by_name['Jacobian'] = _JACOBIAN
DESCRIPTOR.message_types_by_name['KeypointInfo'] = _KEYPOINTINFO
_sym_db.RegisterFileDescriptor(DESCRIPTOR)

Keypoint = _reflection.GeneratedProtocolMessageType('Keypoint', (_message.Message,), dict(
  DESCRIPTOR = _KEYPOINT,
  __module__ = 'keypoints_pb2'
  # @@protoc_insertion_point(class_scope:keypoint.Keypoint)
  ))
_sym_db.RegisterMessage(Keypoint)

Jacobian = _reflection.GeneratedProtocolMessageType('Jacobian', (_message.Message,), dict(
  DESCRIPTOR = _JACOBIAN,
  __module__ = 'keypoints_pb2'
  # @@protoc_insertion_point(class_scope:keypoint.Jacobian)
  ))
_sym_db.RegisterMessage(Jacobian)

KeypointInfo = _reflection.GeneratedProtocolMessageType('KeypointInfo', (_message.Message,), dict(
  DESCRIPTOR = _KEYPOINTINFO,
  __module__ = 'keypoints_pb2'
  # @@protoc_insertion_point(class_scope:keypoint.KeypointInfo)
  ))
_sym_db.RegisterMessage(KeypointInfo)


# @@protoc_insertion_point(module_scope)
