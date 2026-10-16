# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.


def acceptor(cls):
    """
    A decorator that adds an `accept` method to the class.
    The `accept` method calls the appropriate handler method
    of a Dispatcher.

    Using a decorator here allows us to compute the handler name once
    for the class rather than each time we dispatch.
    """
    attr_name = f"visit_{cls.__name__.lower()}"

    def accept(self, v, parent=None):
        return getattr(v, attr_name)(self, parent)

    cls.accept = accept
    return cls


class Node:
    """Represents a node in an Expression tree"""

    @property
    def kind(self):
        return self.__class__.__name__.lower()

    def accept(self, v, parent=None):
        raise NotImplementedError(f"accept not implemented for {self.kind}")