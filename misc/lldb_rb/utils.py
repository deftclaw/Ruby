from lldb_rb.lldb_interface import LLDBInterface
from lldb_rb.rb_heap_structs import HeapPage, RbObject
from lldb_rb.constants import *

class RbInspector(LLDBInterface):
    def __init__(self, debugger, result, ruby_globals):
        self.build_environment(debugger)
        self.result = result
        self.ruby_globals = ruby_globals

    def _append_command_output(self, command):
        output1 = self.result.GetOutput()
        self.debugger.GetCommandInterpreter().HandleCommand(command, self.result)
        output2 = self.result.GetOutput()
        self.result.Clear()
        self.result.write(output1)
        self.result.write(output2)

    def string2cstr(self, rstring):
        """Returns the pointer to the C-string in the given String object"""
        if rstring.TypeIsPointerType():
            rstring = rstring.Dereference()

        flags = rstring.GetValueForExpressionPath(".basic->flags").unsigned
        if flags & self.ruby_globals["RUBY_FL_USER1"]:
            cptr = int(rstring.GetValueForExpressionPath(".as.heap.ptr").value, 0)
            clen = int(rstring.GetValueForExpressionPath(".as.heap.len").value, 0)
        else:
            cptr = int(rstring.GetValueForExpressionPath(".as.embed.ary").location, 0)
            clen = int(rstring.GetValueForExpressionPath(".as.embed.len").value, 0)

        return cptr, clen

    def output_string(self, rstring):
        cptr, clen = self.string2cstr(rstring)
        expr = "print *(const char (*)[%d])%0#x" % (clen, cptr)
        self._append_command_output(expr)

    def fixnum_p(self, x):
        return x & self.ruby_globals["RUBY_FIXNUM_FLAG"] != 0

    def flonum_p(self, x):
        return (x & self.ruby_globals["RUBY_FLONUM_MASK"]) == self.ruby_globals["RUBY_FLONUM_FLAG"]

    def static_sym_p(self, x):
        special_shift = self.ruby_globals["RUBY_SPECIAL_SHIFT"]
        symbol_flag = self.ruby_globals["RUBY_SYMBOL_FLAG"]
        return (x & ~(~0 << special_shift)) == symbol_flag

    def generic_inspect(self, val, rtype):
        tRType = self.target.FindFirstType("struct %s" % rtype).GetPointerType()
        val = val.Cast(tRType)
        self._append_command_output("p *(struct %s *) %0#x" % (rtype, val.GetValueAsUnsigned()))

    def inspect(self, val):
        rbTrue  = self.ruby_globals["RUBY_Qtrue"]
        rbFalse = self.ruby_globals["RUBY_Qfalse"]
        rbNil   = self.ruby_globals["RUBY_Qnil"]
        rbUndef = self.ruby_globals["RUBY_Qundef"]
        rbImmediateMask = self.ruby_globals["RUBY_IMMEDIATE_MASK"]

        num = val.GetValueAsSigned()
        if num == rbFalse:
            print('false', file=self.result)
        elif num == rbTrue:
            print('true', file=self.result)
        elif num == rbNil:
            print('nil', file=self.result)
        elif num == rbUndef:
            print('undef', file=self.result)
        elif self.fixnum_p(num):
            print(num >> 1, file=self.result)
        elif self.flonum_p(num):
            self._append_command_output("print rb_float_value(%0#x)" % val.GetValueAsUnsigned())
        elif self.static_sym_p(num):
            if num < 128:
                print("T_SYMBOL: %c" % num, file=self.result)
            else:
                print("T_SYMBOL: (%x)" % num, file=self.result)
                self._append_command_output("p rb_id2name(%0#x)" % (num >> 8))

        elif num & rbImmediateMask:
            print('immediate(%x)' % num, file=self.result)
        else:
            rval = RbObject(val, self.debugger, self.ruby_globals)
            rval.dump_bits(self.result)

            flaginfo = ""
            if rval.promoted_p():
                flaginfo += "[PROMOTED] "
            if rval.frozen_p():
                flaginfo += "[FROZEN] "

            if rval.is_type("RUBY_T_NONE"):
                print('T_NONE: %s%s' % (flaginfo, val.Dereference()), file=self.result)

            elif rval.is_type("RUBY_T_NIL"):
                print('T_NIL: %s%s' % (flaginfo, val.Dereference()), file=self.result)

            elif rval.is_type("RUBY_T_OBJECT"):
                self.result.write('T_OBJECT: %s' % flaginfo)
                self._append_command_output("print *(struct RObject*)%0#x" % val.GetValueAsUnsigned())

            elif (rval.is_type("RUBY_T_CLASS") or
                  rval.is_type("RUBY_T_MODULE") or
                  rval.is_type("RUBY_T_ICLASS")):
                self.result.write('T_%s: %s' % (rval.type_name.split('_')[-1], flaginfo))
                tRClass = self.target.FindFirstType("struct RClass")

                self._append_command_output("print *(struct RClass*)%0#x" % val.GetValueAsUnsigned())
                if not val.Cast(tRClass).GetChildMemberWithName("ptr").IsValid():
                    self._append_command_output(
                        "print *(struct rb_classext_struct*)%0#x" %
                        (val.GetValueAsUnsigned() + tRClass.GetByteSize())
                    )

            elif rval.is_type("RUBY_T_STRING"):
                self.result.write('T_STRING: %s' % flaginfo)
                tRString = self.target.FindFirstType("struct RString").GetPointerType()

                rb_enc_mask = self.ruby_globals["RUBY_ENCODING_MASK"]
                rb_enc_shift = self.ruby_globals["RUBY_ENCODING_SHIFT"]
                encidx = ((rval.flags & rb_enc_mask) >> rb_enc_shift)
                encname = self.target.FindFirstType("enum ruby_preserved_encindex") \
                        .GetEnumMembers().GetTypeEnumMemberAtIndex(encidx) \
                        .GetName()

                if encname is not None:
                    self.result.write('[%s] ' % encname[14:])
                else:
                    self.result.write('[enc=%d] ' % encidx)

                ptr, len = self.string2cstr(val.Cast(tRString))
                if len == 0:
                    self.result.write("(empty)\n")
                else:
                    self._append_command_output("print *(const char (*)[%d])%0#x" % (len, ptr))

            elif rval.is_type("RUBY_T_SYMBOL"):
                self.result.write('T_SYMBOL: %s' % flaginfo)
                tRSymbol = self.target.FindFirstType("struct RSymbol").GetPointerType()
                tRString = self.target.FindFirstType("struct RString").GetPointerType()

                val = val.Cast(tRSymbol)
                self._append_command_output(
                        'print (ID)%0#x ' % val.GetValueForExpressionPath("->id").GetValueAsUnsigned())
                self.output_string(val.GetValueForExpressionPath("->fstr").Cast(tRString))

            elif rval.is_type("RUBY_T_ARRAY"):
                tRArray = self.target.FindFirstType("struct RArray").GetPointerType()
                len = rval.ary_len().GetValueAsUnsigned();
                ptr = rval.ary_ptr().GetValueAsUnsigned();

                self.result.write("T_ARRAY: %slen=%d" % (flaginfo, len))

                if rval.flags & self.ruby_globals["RUBY_FL_USER1"]:
                    self.result.write(" (embed)")
                elif rval.flags & self.ruby_globals["RUBY_FL_USER2"]:
                    shared = val.GetValueForExpressionPath("->as.heap.aux.shared").GetValueAsUnsigned()
                    self.result.write(" (shared) shared=%016x" % shared)
                else:
                    capa = val.GetValueForExpressionPath("->as.heap.aux.capa").GetValueAsSigned()
                    self.result.write(" (ownership) capa=%d" % capa)
                if len == 0:
                    self.result.write(" {(empty)}\n")
                else:
                    self.result.write("\n")
                    if ptr.GetValueAsSigned() == 0:
                        self._append_command_output(
                                "expression -fx -- ((struct RArray*)%0#x)->as.ary" % val.GetValueAsUnsigned())
                    else:
                        self._append_command_output(
                                "expression -Z %d -fx -- (const VALUE*)%0#x" % (len, ptr.GetValueAsUnsigned()))

            elif rval.is_type("RUBY_T_HASH"):
                self.result.write("T_HASH: %s" % flaginfo)
                self._append_command_output("p *(struct RHash *) %0#x" % val.GetValueAsUnsigned())

            elif rval.is_type("RUBY_T_BIGNUM"):
                tRBignum = self.target.FindFirstType("struct RBignum").GetPointerType()

                sign = '-'
                if (rval.flags & self.ruby_globals["RUBY_FL_USER1"]) != 0:
                    sign = '+'
                len = rval.bignum_len()

                if rval.flags & self.ruby_globals["RUBY_FL_USER2"]:
                    print("T_BIGNUM: sign=%s len=%d (embed)" % (sign, len), file=self.result)
                    self._append_command_output("print ((struct RBignum *) %0#x)->as.ary"
                                                % val.GetValueAsUnsigned())
                else:
                    print("T_BIGNUM: sign=%s len=%d" % (sign, len), file=self.result)
                    print(val.Dereference(), file=self.result)
                    self._append_command_output(
                            "expression -Z %x -fx -- (const BDIGIT*)((struct RBignum*)%d)->as.heap.digits" %
                            (len, val.GetValueAsUnsigned()))

            elif rval.is_type("RUBY_T_FLOAT"):
                self._append_command_output("print ((struct RFloat *)%d)->float_value"
                                            % val.GetValueAsUnsigned())

            elif rval.is_type("RUBY_T_RATIONAL"):
                tRRational = self.target.FindFirstType("struct RRational").GetPointerType()
                val = val.Cast(tRRational)
                self.inspect(val.GetValueForExpressionPath("->num"))
                output = self.result.GetOutput()
                self.result.Clear()
                self.result.write("(Rational) " + output.rstrip() + " / ")
                self.inspect(val.GetValueForExpressionPath("->den"))

            elif rval.is_type("RUBY_T_COMPLEX"):
                tRComplex = self.target.FindFirstType("struct RComplex").GetPointerType()
                val = val.Cast(tRComplex)
                self.inspect(val.GetValueForExpressionPath("->real"))
                real = self.result.GetOutput().rstrip()
                self.result.Clear()
                self.inspect(val.GetValueForExpressionPath("->imag"))
                imag = self.result.GetOutput().rstrip()
                self.result.Clear()
                if not imag.startswith("-"):
                    imag = "+" + imag
                print("(Complex) " + real + imag + "i", file=self.result)

            elif rval.is_type("RUBY_T_REGEXP"):
                tRRegex = self.target.FindFirstType("struct RRegexp").GetPointerType()
                val = val.Cast(tRRegex)
                print("(Regex) ->src {", file=self.result)
                self.inspect(val.GetValueForExpressionPath("->src"))
                print("}", file=self.result)

            elif rval.is_type("RUBY_T_DATA"):
                tRTypedData = self.target.FindFirstType("struct RTypedData").GetPointerType()
                val = val.Cast(tRTypedData)
                flag = val.GetValueForExpressionPath("->typed_flag")

                if flag.GetValueAsUnsigned() == 1:
                    print("T_DATA: %s" %
                          val.GetValueForExpressionPath("->type->wrap_struct_name"),
                          file=self.result)
                    self._append_command_output(
                            "p *(struct RTypedData *) %0#x" % val.GetValueAsUnsigned())
                else:
                    print("T_DATA:", file=self.result)
                    self._append_command_output(
                            "p *(struct RData *) %0#x" % val.GetValueAsUnsigned())

            elif rval.is_type("RUBY_T_NODE"):
                tRNode = self.target.FindFirstType("struct RNode").GetPointerType()
                rbNodeTypeMask = self.ruby_globals["RUBY_NODE_TYPEMASK"]
                rbNodeTypeShift = self.ruby_globals["RUBY_NODE_TYPESHIFT"]

                nd_type = (rval.flags & rbNodeTypeMask) >> rbNodeTypeShift
                val = val.Cast(tRNode)

                self._append_command_output("p (node_type) %d" % nd_type)
                self._append_command_output("p *(struct RNode *) %0#x" % val.GetValueAsUnsigned())

            elif rval.is_type("RUBY_T_IMEMO"):
                imemo_type = ((rval.flags >> self.ruby_globals["RUBY_FL_USHIFT"])
                              & IMEMO_MASK)
                print("T_IMEMO: ", file=self.result)

                self._append_command_output("p (enum imemo_type) %d" % imemo_type)
                self._append_command_output("p *(struct MEMO *) %0#x" % val.GetValueAsUnsigned())

            elif rval.is_type("RUBY_T_FILE"):
                self.generic_inspect(val, "RFile")

            elif rval.is_type("RUBY_T_MOVED"):
                self.generic_inspect(val, "RMoved")

            elif rval.is_type("RUBY_T_MATCH"):
                self.generic_inspect(val, "RMatch")

            elif rval.is_type("RUBY_T_STRUCT"):
                self.generic_inspect(val, "RStruct")

            elif rval.is_type("RUBY_T_ZOMBIE"):
                self.generic_inspect(val, "RZombie")

            else:
                print("Not-handled type %0#x" % rval.type, file=self.result)
                print(val, file=self.result)

